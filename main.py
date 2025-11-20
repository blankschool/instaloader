import os
import time
import uuid
import shutil
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel

import instaloader
from instaloader.exceptions import (
    BadResponseException,
    QueryReturnedNotFoundException,
)

# ----------------------------------------------------------
# CONFIGURAÇÃO
# ----------------------------------------------------------

INSTAGRAM_USER = "grupocrasto"

BASE_DIR = Path(__file__).resolve().parent
SESSION_DIR = BASE_DIR / "instaloader"
SESSION_FILE = SESSION_DIR / f"session-{INSTAGRAM_USER}"

L = instaloader.Instaloader()

# ----------------------------------------------------------
# CARREGAR SESSÃO (MAS NÃO TRAVAR SE DER ERRO)
# ----------------------------------------------------------

print("------------------------------------------------------")
print("Tentando carregar sessão do Instagram...")
print("Caminho esperado:", SESSION_FILE)
print("Existe o arquivo?", SESSION_FILE.exists())
print("------------------------------------------------------")

SESSION_LOADED = False

try:
    if SESSION_FILE.exists():
        L.load_session_from_file(INSTAGRAM_USER, str(SESSION_FILE))
        user = L.test_login()
        if user:
            print("Sessão carregada com sucesso. Logado como:", user)
            SESSION_LOADED = True
        else:
            print("⚠️ Sessão carregada, mas test_login() retornou None.")
    else:
        print("⚠️ Arquivo de sessão não encontrado.")

except Exception as e:
    print("⚠️ Erro ao carregar sessão:", e)

print("------------------------------------------------------")
print("STATUS FINAL DA SESSÃO:", SESSION_LOADED)
print("------------------------------------------------------")

# ----------------------------------------------------------
# FASTAPI
# ----------------------------------------------------------

app = FastAPI(
    title="Instagram Downloader",
    description="Download de posts, reels e carrosséis usando Instaloader (com ou sem sessão)",
    version="1.0.0",
)


class PostRequest(BaseModel):
    url: str


# ----------------------------------------------------------
# UTILITÁRIOS
# ----------------------------------------------------------

def shortcode_from_url(url: str) -> str:
    """Extrai o shortcode da URL do Instagram."""
    url = url.split("?")[0]
    parts = [p for p in url.split("/") if p]
    return parts[-1]


def get_post_with_retry(shortcode: str, max_retries: int = 3):
    """Tenta carregar o post com retry/backoff para rate limit."""
    last_err = None

    for attempt in range(max_retries):
        try:
            return instaloader.Post.from_shortcode(L.context, shortcode)

        except BadResponseException as e:
            msg = str(e)
            last_err = e

            if "Please wait a few minutes" in msg:
                wait = 30 * (attempt + 1)
                print(f"[RATE LIMIT] Tentativa {attempt+1}/{max_retries}. Aguardando {wait}s...")
                time.sleep(wait)
                continue

            raise

        except QueryReturnedNotFoundException as e:
            raise HTTPException(status_code=404, detail="Post não encontrado") from e

    raise HTTPException(
        status_code=429,
        detail="Instagram bloqueou temporariamente. Tente novamente."
    ) from last_err


# ----------------------------------------------------------
# ENDPOINTS
# ----------------------------------------------------------

@app.get("/health")
def health():
    return {
        "status": "ok",
        "session_loaded": SESSION_LOADED,
        "logged_as": L.test_login() if SESSION_LOADED else None
    }


@app.post("/post_info")
def post_info(req: PostRequest):
    shortcode = shortcode_from_url(req.url)

    try:
        post = get_post_with_retry(shortcode)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao ler post: {e}")

    return {
        "shortcode": shortcode,
        "username": post.owner_username,
        "caption": post.caption,
        "is_video": post.is_video,
        "slides": post.mediacount,
    }


@app.post("/download_post")
def download_post(req: PostRequest):
    shortcode = shortcode_from_url(req.url)

    tmp = Path("/tmp") / f"ig-{uuid.uuid4()}"
    tmp.mkdir(parents=True, exist_ok=True)

    try:
        post = get_post_with_retry(shortcode)

        target = tmp / shortcode
        target.mkdir(parents=True, exist_ok=True)

        L.download_post(post, target=str(target))

        media_files: List[Path] = [
            f for f in target.iterdir()
            if f.suffix.lower() in [".mp4", ".jpg", ".jpeg", ".png", ".webp"]
        ]

        if not media_files:
            raise HTTPException(status_code=500, detail="Nenhuma mídia encontrada.")

        # CARROSSEL = ZIP
        if len(media_files) > 1:
            zip_path = tmp / f"{shortcode}.zip"
            shutil.make_archive(str(zip_path.with_suffix("")), "zip", target)
            data = zip_path.read_bytes()

            return Response(
                content=data,
                media_type="application/zip",
                headers={
                    "Content-Disposition": f'attachment; filename="{shortcode}.zip"'
                }
            )

        # APENAS 1 ARQUIVO
        file = media_files[0]
        data = file.read_bytes()

        mimetype = {
            ".mp4": "video/mp4",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }.get(file.suffix.lower(), "application/octet-stream")

        return Response(
            content=data,
            media_type=mimetype,
            headers={"Content-Disposition": f'attachment; filename="{file.name}"'}
        )

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao baixar: {e}")

    finally:
        try:
            shutil.rmtree(tmp)
        except:
            pass
