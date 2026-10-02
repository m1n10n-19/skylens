"""
The backend serves the frontend (single-service hosting on Render).
"""


def test_frontend_is_revalidated_on_every_load(client):
    """
    After a deploy, a cached old script must not run next to new ones.
    """

    for path in ("/", "/js/util.js", "/css/app.css"):

        response = client.get(path)

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache"
