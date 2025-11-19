from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from instaloader import Instaloader, Post

app = FastAPI(
    title="Instaloader Microservice",
    description="Serviço simples para pegar dados de posts do Instagram via Instaloader",
    version="1.0.0",
)

# Instância global do Instaloader (não baixa nada, só usa como client)
L = Instaloader(
    download_pictures=False,
    download_videos=False,
    download_video_thumbnails=False,
    download_comments=False,
    save_metadata=False,
    compress_json=False,
)


class PostRequest(BaseModel):
    url: str


def extract_shortcode_from_url(url: str) -> str | None:
    """
    Recebe uma URL de post do Instagram e extrai o shortcode.
    Exemplo:
      https://www.instagram.com/p/ABC123xyz/ -> ABC123xyz
    """
    base = url.split("?")[0]
    parts = [p for p in base.split("/") if p]
    if not parts:
        return None
    shortcode = parts[-1]
    if shortcode in ("p", "reel", "tv") and len(parts) >= 2:
        shortcode = parts[-1]
    return shortcode or None


@app.post("/download_post")
def download_post(req: PostRequest):
    """
    Recebe { "url": "https://www.instagram.com/p/..." }
    e devolve infos do post + URL direta da mídia
    """
    shortcode = extract_shortcode_from_url(req.url)
    if not shortcode:
        raise HTTPException(status_code=400, detail="URL de post inválida")

    try:
        post = Post.from_shortcode(L.context, shortcode)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao acessar o post: {e}")

    media_url = post.video_url if post.is_video else post.url

    return {
        "shortcode": shortcode,
        "caption": post.caption or "",
        "is_video": post.is_video,
        "media_url": media_url,
        "owner_username": post.owner_username,
        "taken_at": post.date_utc.isoformat(),
    }


@app.get("/health")
def health():
    return {"status": "ok"}
