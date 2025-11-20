import os
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import instaloader
from instaloader.exceptions import BadResponseException, QueryReturnedNotFoundException

# ---------- CONFIG ----------

INSTAGRAM_USER = "grupocrasto"
BASE_DIR = Path(__file__).parent
SESSION_FILE = BASE_DIR / "instaloader" / f"session-{INSTAGRAM_USER}"

# ---------- INSTALOADER GLOBAL ----------

L = instaloader.Instaloader()

try:
    # carrega a sessão que você subiu pro repo
    L.load_session_from_file(INSTAGRAM_USER, str(SESSION_FILE))
    print("Sessão Instagram carregada com sucesso.")
    print("Logado como:", L.test_login())
except Exception as e:
    print("ERRO ao carregar sessão do Instagram:", e)
    # aqui eu não levanto exceção pra não quebrar o container na subida,
    # mas os endpoints vão falhar até você corrigir
    # se quiser ser mais rígido, troque por: raise

# ---------- FASTAPI ----------

app = FastAPI(
    title="Instaloader Microservice",
    description="Baixa/consulta posts do Instagram usando sessão grupocrasto",
    version="1.0.0",
)


class PostRequest(BaseModel):
    url: str


def shortcode_from_url(url: str) -> str:
    """
    Extrai o shortcode de URLs do tipo:
    https://www.instagram.com/p/DRNRfHRD4VF/
    https://www.instagram.com/reel/DRNRfHRD4VF/
    https://www.instagram.com/p/DRNRfHRD4VF/?utm_source=...
    """
    parts = url.split("/")
    # tira parâmetros
    parts = [p for p in parts if p]
    # shortcode é o último pedaço "não vazio" da URL (p/reel/p)
    shortcode = parts[-1]
    # se vier com querystring, remove
    if "?" in shortcode:
        shortcode = shortcode.split("?", 1)[0]
    return shortcode


def get_post_with_retry(shortcode: str, max_retries: int = 3):
    """
    Envolve o Post.from_shortcode com retry/backoff
    pra tratar "Please wait a few minutes before you try again".
    """
    last_err = None
    for attempt in range(max_retries):
        try:
            return instaloader.Post.from_shortcode(L.context, shortcode)
        except BadResponseException as e:
            msg = str(e)
            last_err = e
            if "Please wait a few minutes before you try again" in msg:
                wait = 60 * (attempt + 1)  # 1min, depois 2min, depois 3min...
                print(f"[Rate limit IG] Tentativa {attempt+1}/{max_retries}, "
                      f"esperando {wait}s...")
                time.sleep(wait)
                continue
            raise
        except QueryReturnedNotFoundException as e:
            raise HTTPException(status_code=404, detail="Post não encontrado") from e

    # se chegou aqui, estourou as tentativas
    raise HTTPException(
        status_code=429,
        detail="Instagram aplicou rate limit. Tente novamente mais tarde.",
    ) from last_err


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/post_info")
def post_info(body: PostRequest):
    """
    Recebe uma URL de post/reel e retorna informações básicas.
    Exemplo de body:
    {
      "url": "https://www.instagram.com/p/DRNRfHRD4VF/"
    }
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
        "slides_count": post.mediacount,  # qtd de imagens/vídeos no carrossel
    }
