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
    e devolve infos do post + TODAS as mídias (carrossel, vídeo, imagem única)
    """
    shortcode = extract_shortcode_from_url(req.url)
    if not shortcode:
        raise HTTPException(status_code=400, detail="URL de post inválida")

    try:
        post = Post.from_shortcode(L.context, shortcode)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao acessar o post: {e}. "
                   f"Verifique se o post é público e se o login foi configurado."
        )

    # Monta lista de mídias
    media_list = []

    # Verifica se é carrossel (sidecar)
    if post.typename == "GraphSidecar":
        # Post com várias imagens / vídeos (carrossel)
        for idx, node in enumerate(post.get_sidecar_nodes()):
            url = node.video_url if node.is_video else node.display_url
            media_list.append({
                "index": idx,
                "is_video": node.is_video,
                "media_url": url,
            })
    else:
        # Post “normal” (uma imagem ou um vídeo)
        url = post.video_url if post.is_video else post.url
        media_list.append({
            "index": 0,
            "is_video": post.is_video,
            "media_url": url,
        })

    # Pra manter compatibilidade, ainda mandamos o primeiro media_url no topo
    first_media_url = media_list[0]["media_url"] if media_list else None

    return {
        "shortcode": shortcode,
        "caption": post.caption or "",
        "owner_username": post.owner_username,
        "taken_at": post.date_utc.isoformat(),
        "is_sidecar": post.typename == "GraphSidecar",
        "media": media_list,          # TODAS as mídias
        "media_url": first_media_url, # primeira mídia (legacy)
    }
    
@app.get("/health")
def health():
    return {"status": "ok"}
