"""Vetting blueprint routes."""
from flask import Blueprint, render_template, abort
from flask_login import login_required, current_user

from features import user_has_feature

vetting_bp = Blueprint("vetting", __name__, url_prefix="/vetting")


@vetting_bp.before_request
def _gate():
    """Feature gate：只有具 'vetting' feature 的使用者可進入。
    admin (is_root_admin) bypass 由 user_has_feature 內建。"""
    if current_user.is_authenticated and not user_has_feature(current_user, "vetting"):
        abort(404)


@vetting_bp.route("/")
@login_required
def index():
    return render_template("vetting/index.html")
