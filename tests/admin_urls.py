from django.contrib import admin
from django.urls import path

from django_tasks_aws.admin import register_admin

custom_site = admin.AdminSite(name="custom")
register_admin(custom_site)
urlpatterns = [path("admin/", admin.site.urls), path("custom/", custom_site.urls)]
