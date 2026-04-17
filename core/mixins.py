"""Pagination mixin — session-persistent page size (20/50/100/150), default 50."""

from django.conf import settings
from django.views.generic import ListView


class PaginatedListView(ListView):
    """ListView with session-persistent page size selection.

    Append `?page_size=100` to the URL to change. The chosen size is stored in
    the session keyed by `paginate_session_key` (default: the view class name).
    """

    paginate_by = settings.PAGINATION_DEFAULT
    allowed_page_sizes = settings.PAGINATION_PAGE_SIZES
    paginate_session_key = None

    def get_paginate_by(self, queryset):
        key = self.paginate_session_key or f"page_size:{self.__class__.__name__}"
        requested = self.request.GET.get("page_size")
        if requested:
            try:
                size = int(requested)
            except (TypeError, ValueError):
                size = None
            if size in self.allowed_page_sizes:
                self.request.session[key] = size
                return size
        stored = self.request.session.get(key)
        if stored in self.allowed_page_sizes:
            return stored
        return self.paginate_by

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["page_size_options"] = self.allowed_page_sizes
        ctx["current_page_size"] = self.get_paginate_by(self.object_list)
        return ctx
