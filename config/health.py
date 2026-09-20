from django.db import connection
from django.http import JsonResponse


class HealthCheckMiddleware:
    """Answers /health/ before host validation; confirms DB connectivity."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == "/health/":
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
            except Exception:
                return JsonResponse({"status": "db_error"}, status=503)
            return JsonResponse({"status": "ok"})
        return self.get_response(request)
