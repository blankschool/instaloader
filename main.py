import os
import time
import uuid
import shutil
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
import instaloader
from instaloader.exceptions import BadResponseException, QueryReturnedNotFoundException

# -------- CONFIG BÁSICA --------

INSTAGRAM_USER = "grupocrasto"
BASE_DIR = Path(__file__).parent
SESSION_FILE = BASE_DIR / "instaloader" / f"session-{INSTAGRAM_USER}"

# -------- INSTALOADER GLOBAL --------

L = instaloader.Instaloader()

try:
    L.load_session_from_file(INSTAGRAM_USER, str(SESSION_FILE))
    print("Sessão Instagram carregada com sucesso.")
    print("Logado como:", L.test_login())
except Exception as e:
    print("ERRO ao carregar sessão do Instagram:", e)
    # se quiser travar o serviço se der erro de sessão, descomenta:
    # raise

# -------- FASTAPI --------

app = FastAPI(
    title="Instaloader Downloader",
    description="Download de posts/reels/carrosséis do Instagram usando sessão grupocrasto",
    version="1.0.0",
)


class PostRequest(BaseModel):
    url: str


def shortcode_from_url(url: str) -> str:
    """
    Extrai shortcode de URLs de post/reel:
    https://www.instagram.com/p/XXXX/
    https://www.instagram.com/reel/XXXX/?utm_source=...
    """
    url = url.split("?")[0]
    parts = [p for p in url.split("/") if p]
    # último pedaço não vazio
    shortcode = parts[-1]
    return shortcode


def get_post_with_retry(shortcode: str, max_retries: int = 3):
    """
    Envolve Post.from_shortcode com retry/backoff para tratar
    o erro 'Please wait a few minutes before you try again'.
    """
    last_err: Optional[Exception] = None

    for attempt in range(max_retries):
        try:
            return instaloader.Post.from_shortcode(L.context, shortcode)
        except BadResponseException as e:
            msg = str(e)
            last_err = e
            if "Please wait a few minutes before you try again" in msg:
                wait = 60 * (attempt + 1)  # 1 min, depois 2, depois 3...
                print(f"[Rate limit IG] Tentativa {attempt+1}/{max_retries}, "
                      f"esperando {wait}s...")
                time.sleep(wait)
                continue
            # outro tipo de erro do IG
            raise
        except QueryReturnedNotFoundException as e:
            raise HTTPException(status_code=404, detail="Post não encontrado") from e

    # se esgotou tentativas
    raise HTTPException(
        status_code=429,
        detail="Instagram aplicou rate limit. Tente novamente mais tarde.",
    ) from last_err


@app.get("/health")
def health():
    return {"status": "ok", "instagram_user": L.test_login()}


@app.post("/post_info")
def post_info(body: PostRequest):
    """
    Retorna informações básicas do post (para debug/teste).
    """
    if not L.test_login():
        raise HTTPException(
            status_code=500,
            detail="Sessão do Instagram não carregada. Verifique o arquivo de sessão.",
        )

    shortcode = shortcode_from_url(body.url)

    try:
        post = get_post_with_retry(shortcode)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao acessar o post: {str(e)}",
        )

    return {
        "shortcode": shortcode,
        "owner_username": post.owner_username,
        "caption": post.caption,
        "is_video": post.is_video,
        "date_utc": post.date_utc.isoformat() if post.date_utc else None,
        "likes": post.likes,
        "comments": post.comments,
        "slides_count": post.mediacount,
    }


@app.post("/download_post")
def download_post(body: PostRequest):
    """
    Baixa o post (reel/carrossel) e retorna o arquivo:

    - Se for 1 mídia: retorna o próprio arquivo (mp4/jpg)
    - Se for carrossel (várias): compacta em .zip e retorna o zip
    """
    if not L.test_login():
        raise HTTPException(
            status_code=500,
            detail="Sessão do Instagram não carregada. Verifique o arquivo de sessão.",
        )

    shortcode = shortcode_from_url(body.url)

    # pasta temporária
    tmp_dir = Path("/tmp") / f"ig-{uuid.uuid4()}"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        post = get_post_with_retry(shortcode)

        # target é a pasta onde o Instaloader vai jogar os arquivos
        target_dir = tmp_dir / shortcode
        target_dir.mkdir(parents=True, exist_ok=True)

        # baixa
        L.download_post(post, target=str(target_dir))

        # pega todos os arquivos de mídia
        media_files: List[Path] = []
        for f in target_dir.iterdir():
            if f.suffix.lower() in [".jpg", ".jpeg", ".png", ".webp", ".mp4"]:
                media_files.append(f)

        if not media_files:
            raise HTTPException(
                status_code=500,
                detail="Nenhum arquivo de mídia foi baixado do post.",
            )

        if len(media_files) == 1:
            # um único arquivo: retorna direto
            media_file = media_files[0]
            content = media_file.read_bytes()

            # tipo MIME aproximado
            if media_file.suffix.lower() == ".mp4":
                mime = "video/mp4"
            elif media_file.suffix.lower() in [".jpg", ".jpeg"]:
                mime = "image/jpeg"
            elif media_file.suffix.lower() == ".png":
                mime = "image/png"
            else:
                mime = "application/octet-stream"

            filename = media_file.name

            return Response(
                content=content,
                media_type=mime,
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        else:
            # mais de um arquivo: carrossel → zip
            zip_path = tmp_dir / f"{shortcode}.zip"
            shutil.make_archive(str(zip_path.with_suffix("")), "zip", target_dir)

            zip_bytes = zip_path.read_bytes()

            return Response(
                content=zip_bytes,
                media_type="application/zip",
                headers={
                    "Content-Disposition": f'attachment; filename="{shortcode}.zip"'
                },
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao baixar o post: {str(e)}",
        )
    finally:
        # limpa tmp
        try:
            shutil.rmtree(tmp_dir)
        except Exception:
            pass
