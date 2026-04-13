"""My Notes 個人筆記模組（開發中空殼）"""
from flask import Blueprint, render_template
from flask_login import login_required
from features import require_feature

mynotes_bp = Blueprint("mynotes", __name__, url_prefix="/mynotes")


@mynotes_bp.route("/")
@login_required
def index():
    require_feature("mynotes")
    return render_template("mynotes/placeholder.html")
