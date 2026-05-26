from . import routes
from . import announcement_routes  # noqa: F401  (公告路由註冊到 notes_bp)
from . import tags  # noqa: F401  (標籤路由註冊到 notes_bp)

from notes.tag_palette import pick_fg as _pick_fg
routes.notes_bp.add_app_template_filter(_pick_fg, 'pick_fg')
