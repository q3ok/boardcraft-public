from fastapi.staticfiles import StaticFiles


class PageStaticFiles(StaticFiles):
    """Static files whose HTML pages are revalidated on every load, so a new build is seen at once."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache"
        return response
