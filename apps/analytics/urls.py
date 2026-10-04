from django.urls import path

from . import views

urlpatterns = [
    path("analytics/kpis", views.KpiView.as_view(), name="analytics-kpis"),
    path("analytics/insights", views.InsightsView.as_view(), name="analytics-insights"),
    path("analytics/anomalies", views.AnomaliesView.as_view(), name="analytics-anomalies"),
    path("analytics/forecast/tresorerie", views.PrevisionTresorerieView.as_view(), name="analytics-forecast"),
    path("analytics/copilote", views.CopiloteView.as_view(), name="analytics-copilote"),
]
