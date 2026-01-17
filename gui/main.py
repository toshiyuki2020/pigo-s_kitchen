from __future__ import annotations

# --- 追加import（先頭の import 群に足す） ---
import os
from dataclasses import dataclass, field

from pathlib import Path

from PyQt6.QtGui import QBrush, QColor, QCursor
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QTreeView,
    QMenu,
)


# --- WizardState を拡張（既存dataclassを差し替え） ---
@dataclass
class WizardState:
    project_root: Path | None = None
    project_name: str = ""
    project_purpose: str = ""
    project_policy: str = ""
    # 明示指定ルールだけ保存（rel_posix -> "deny"/"allow"）
    project_rules: dict[str, str] = field(default_factory=dict)


# ===== RulesPage 実装 =====

PATH_ROLE = int(Qt.ItemDataRole.UserRole) + 1
RULE_ROLE = int(Qt.ItemDataRole.UserRole) + 2
IS_DIR_ROLE = int(Qt.ItemDataRole.UserRole) + 3
LOADED_ROLE = int(Qt.ItemDataRole.UserRole) + 4
PLACEHOLDER_ROLE = int(Qt.ItemDataRole.UserRole) + 5

RULE_INHERIT  = 0  # 継承（明示なし）
RULE_EXCLUDE  = 1  # 除外（mdに出さない / codexでは禁止）
RULE_TREEONLY = 2  # 構造のみ（パスだけ / codexでは基本禁止）
RULE_TEXT     = 3  # 本文あり（mdに本文も出す / codexで編集対象にできる）

BG_EXCLUDE  = QBrush(QColor(255, 100, 100))
BG_TREEONLY = QBrush(QColor(170, 130, 0))
BG_TEXT     = QBrush(QColor(0, 120, 0))


def rule_label(rule: int) -> str:
    return {
        RULE_INHERIT:  "継承",
        RULE_EXCLUDE:  "除外",
        RULE_TREEONLY: "構造のみ",
        RULE_TEXT:     "本文あり",
    }.get(rule, "継承")

def rule_to_token(rule: int) -> str:
    return {
        RULE_EXCLUDE:  "exclude",
        RULE_TREEONLY: "tree",
        RULE_TEXT:     "text",
    }.get(rule, "")

def token_to_rule(token: str) -> int:
    return {
        "exclude": RULE_EXCLUDE,
        "tree":    RULE_TREEONLY,
        "text":    RULE_TEXT,
    }.get(token, RULE_INHERIT)


class RulesPage(QFrame):
    """Step 2: 変更禁止/許可/継承 をツリーで設定"""

    def __init__(self, state: WizardState):
        super().__init__()
        self.state = state

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        title = QLabel("Rules")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        lay.addWidget(title)

        desc = QLabel("ツリーから領域を選んで、変更禁止などのルールを設定できます（右クリック）。")
        desc.setStyleSheet("opacity: 0.85;")
        lay.addWidget(desc)

        # buttons
        btn_row = QHBoxLayout()
        self.btn_reload = QPushButton("再読み込み")
        self.btn_apply_suggest = QPushButton("よくある候補を禁止にする")
        self.btn_clear = QPushButton("明示ルールを全解除")
        btn_row.addWidget(self.btn_reload)
        btn_row.addWidget(self.btn_apply_suggest)
        btn_row.addWidget(self.btn_clear)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        # tree
        self.tree = QTreeView()
        self.tree.setHeaderHidden(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.open_menu)
        self.tree.expanded.connect(self.on_expand)

        from PyQt6.QtGui import QStandardItemModel, QStandardItem
        self.QStandardItem = QStandardItem
        self.model = QStandardItemModel()
        self.model.setHorizontalHeaderLabels(["Path", "Rule"])
        self.tree.setModel(self.model)

        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)

        header = self.tree.header()

        # ユーザーがドラッグで列幅を変更できる
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)  # Path
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)  # Rule

        # Rule列は初期だけ狭くする（ユーザー調整は可能）
        header.resizeSection(1, 70)

        # 長いパスは「…」で省略
        self.tree.setTextElideMode(Qt.TextElideMode.ElideMiddle)

        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.tree.setColumnWidth(0, 680)
        lay.addWidget(self.tree, 1)

        self.status = QLabel("")
        self.status.setStyleSheet("opacity: 0.85;")
        lay.addWidget(self.status)

        self.btn_reload.clicked.connect(self.reload_tree)
        self.btn_apply_suggest.clicked.connect(self.apply_suggestions)
        self.btn_clear.clicked.connect(self.clear_explicit_rules)

        self.initial_expand_depth = 3
        self.max_total_nodes = 5000

        self.reload_tree()

    def _fit_columns(self):
        # viewport幅に合わせて Path列(0) を自動調整
        vw = self.tree.viewport().width()
        rule_w = self.tree.header().sectionSize(1)
        self.tree.header().resizeSection(0, max(200, vw - rule_w - 2))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._fit_columns()

    def preload_to_depth(self, item0, depth: int, counter: list[int]):
        if depth <= 0:
            return
        if counter[0] >= self.max_total_nodes:
            return

        self.load_children(item0)

        # ここで “ディレクトリだけ” 掘る（ファイルはそのまま表示されるのでOK）
        for i in range(item0.rowCount()):
            if counter[0] >= self.max_total_nodes:
                break

            c0 = item0.child(i, 0)
            if not c0:
                continue

            counter[0] += 1

            if c0.data(IS_DIR_ROLE):
                # 展開（見た目も深くなる）
                self.tree.expand(c0.index())
                self.preload_to_depth(c0, depth - 1, counter)

    # ---------- core ----------
    def reload_tree(self):
        self.model.removeRows(0, self.model.rowCount())
        root = self.state.project_root
        if not root or not root.exists():
            self.status.setText("Step1でプロジェクトルートを選択してください。")
            return

        root_item = self.QStandardItem(root.name + "/")
        rule_item = self.QStandardItem(rule_label(RULE_INHERIT))
        root_item.setData(root, PATH_ROLE)
        root_item.setData(RULE_INHERIT, RULE_ROLE)
        root_item.setData(True, IS_DIR_ROLE)
        root_item.setData(False, LOADED_ROLE)

        # 既存stateのルールがあれば反映
        if "" in self.state.project_rules:
            root_item.setData(token_to_rule(self.state.project_rules[""]), RULE_ROLE)

        self.model.appendRow([root_item, rule_item])

        # ★ 直下だけ読み込む
        self.load_children(root_item)

        # ★ rootだけ開いて、直下を見せる（それ以上は開かない）
        self.tree.expand(root_item.index())

        self._fit_columns()

        self.status.setText(f"OK: {root} を読み込みました。")

    def validate(self) -> tuple[bool, str]:
        if self.state.project_root is None:
            return False, "先にプロジェクトルートを選択してください。"
        return True, ""

    def load_children(self, item0):
        if not item0.data(IS_DIR_ROLE):
            return
        if item0.data(LOADED_ROLE):
            return

        if item0.rowCount() == 1:
            c = item0.child(0, 0)
            if c and c.data(PLACEHOLDER_ROLE):
                item0.removeRows(0, 1)

        path: Path = item0.data(PATH_ROLE)

        if not path:
            return

        try:
            entries = list(os.scandir(path))
        except Exception as e:
            self.status.setText(f"読み込み失敗: {e}")
            return

        entries.sort(key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))

        for ent in entries:
            name = ent.name
            if name in {".git", "__pycache__"}:
                continue

            is_dir = ent.is_dir(follow_symlinks=False)
            label = name + ("/" if is_dir else "")
            child0 = self.QStandardItem(label)
            child1 = self.QStandardItem(rule_label(RULE_INHERIT))

            child_path = Path(ent.path)
            child0.setData(child_path, PATH_ROLE)
            child0.setData(RULE_INHERIT, RULE_ROLE)
            child0.setData(is_dir, IS_DIR_ROLE)
            child0.setData(False, LOADED_ROLE)

            if is_dir:
                self._add_placeholder_if_dir(child0)

            rel = self._rel_posix(child_path)
            if rel in self.state.project_rules:
                child0.setData(token_to_rule(self.state.project_rules[rel]), RULE_ROLE)

            item0.appendRow([child0, child1])
            self._sync_row(child0)

        item0.setData(True, LOADED_ROLE)
        self._refresh_effective_colors(item0)

    # ---------- lazy load ----------
    def on_expand(self, index):
        item0 = self.model.itemFromIndex(index.siblingAtColumn(0))
        if not item0:
            return
        self.load_children(item0)
        if not item0.data(IS_DIR_ROLE):
            return
        if item0.data(LOADED_ROLE):
            return

        path: Path = item0.data(PATH_ROLE)
        if not path:
            return

        # 子を生成
        try:
            entries = list(os.scandir(path))
        except Exception as e:
            self.status.setText(f"読み込み失敗: {e}")
            return

        # sort: dirs first
        entries.sort(key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))

        for ent in entries:
            name = ent.name

            # 超巨大で邪魔になりがちなもの（必要ならここを調整）
            if name in {".git", "__pycache__"}:
                continue

            is_dir = ent.is_dir(follow_symlinks=False)
            label = name + ("/" if is_dir else "")
            child0 = self.QStandardItem(label)
            child1 = self.QStandardItem(rule_label(RULE_INHERIT))

            child_path = Path(ent.path)
            child0.setData(child_path, PATH_ROLE)
            child0.setData(RULE_INHERIT, RULE_ROLE)
            child0.setData(is_dir, IS_DIR_ROLE)
            child0.setData(False, LOADED_ROLE)

            # stateに明示ルールがあれば復元（rel_posix）
            rel = self._rel_posix(child_path)
            if rel in self.state.project_rules:
                child0.setData(token_to_rule(self.state.project_rules[rel]), RULE_ROLE)

            item0.appendRow([child0, child1])
            self._sync_row(child0)

        item0.setData(True, LOADED_ROLE)
        self._refresh_effective_colors(item0)

    # ---------- context menu ----------
    def open_menu(self, pos):
        index = self.tree.indexAt(pos)
        if not index.isValid():
            return

        item0 = self.model.itemFromIndex(index.siblingAtColumn(0))
        if not item0:
            return

        global_pos = self.tree.viewport().mapToGlobal(pos)  # ★追加

        menu = QMenu(self)
        act_inherit  = menu.addAction("継承にする（明示指定を外す）")
        act_exclude  = menu.addAction("除外（mdに出さない / codexは禁止）")
        act_treeonly = menu.addAction("構造のみ（パスだけ）")
        act_text     = menu.addAction("本文あり（本文も出す）")

        chosen = menu.exec(global_pos)

        if chosen == act_inherit:
            self.set_explicit_rule(item0, RULE_INHERIT)
        elif chosen == act_exclude:
            self.set_explicit_rule(item0, RULE_EXCLUDE)
        elif chosen == act_treeonly:
            self.set_explicit_rule(item0, RULE_TREEONLY)
        elif chosen == act_text:
            self.set_explicit_rule(item0, RULE_TEXT)

    def set_explicit_rule(self, item0, rule: int):
        item0.setData(rule, RULE_ROLE)
        self._sync_row(item0)
        self._refresh_effective_colors(item0)
        self._export_to_state()

    # ---------- helpers ----------
    def _sync_row(self, item0):
        # rule列を更新
        rule = int(item0.data(RULE_ROLE) or RULE_INHERIT)
        rule_item = self.model.itemFromIndex(item0.index().siblingAtColumn(1))
        if rule_item:
            rule_item.setText(rule_label(rule))

        # 実効ルールで色付け（軽め）
        eff = self.effective_rule(item0)
        bg = QBrush()
        if eff == RULE_EXCLUDE:
            bg = BG_EXCLUDE
        elif eff == RULE_TREEONLY:
            bg = BG_TREEONLY
        elif eff == RULE_TEXT:
            bg = BG_TEXT

        item0.setBackground(bg)
        if rule_item:
            rule_item.setBackground(bg)

    def effective_rule(self, item0) -> int:
        # 自分が明示ならそれ。継承なら親を辿る。
        cur = item0
        while cur:
            r = int(cur.data(RULE_ROLE) or RULE_INHERIT)
            if r != RULE_INHERIT:
                return r
            cur = cur.parent()
        return RULE_INHERIT

    def _refresh_effective_colors(self, item0):
        # item0 と配下（すでにロード済みの範囲）を更新
        stack = [item0]
        while stack:
            it = stack.pop()
            self._sync_row(it)
            for i in range(it.rowCount()):
                child0 = it.child(i, 0)
                if child0:
                    stack.append(child0)

    def _rel_posix(self, p: Path) -> str:
        root = self.state.project_root
        if not root:
            return ""
        try:
            return p.relative_to(root).as_posix()
        except Exception:
            return ""

    def _export_to_state(self):
        # 明示（継承以外）だけ保存
        rules: dict[str, str] = {}

        def walk(item0):
            rule = int(item0.data(RULE_ROLE) or RULE_INHERIT)
            path: Path = item0.data(PATH_ROLE)
            rel = self._rel_posix(path) if path else ""
            if rule in (RULE_TEXT, RULE_EXCLUDE):
                rules[rel] = rule_to_token(rule)

            for i in range(item0.rowCount()):
                c0 = item0.child(i, 0)
                if c0:
                    walk(c0)

        root0 = self.model.item(0, 0)
        if root0:
            # ルートに明示ルールを許可（rel=""）
            rule = int(root0.data(RULE_ROLE) or RULE_INHERIT)
            if rule in (RULE_TEXT, RULE_EXCLUDE):
                rules[""] = rule_to_token(rule)

            for i in range(root0.rowCount()):
                c0 = root0.child(i, 0)
                if c0:
                    walk(c0)

        self.state.project_rules = rules

    def apply_suggestions(self):
        # ルート直下をある程度展開してから、よくある候補を禁止にする
        root0 = self.model.item(0, 0)
        if not root0:
            return

        # root直下が未ロードなら展開してロード
        if not root0.data(LOADED_ROLE):
            self.on_expand(root0.index())

        deny_names = {"vendor", "node_modules", "storage", "var", ".git", "public/build", "dist", "build"}
        # 直下だけ対象（必要なら検索を深くする）
        for i in range(root0.rowCount()):
            child0 = root0.child(i, 0)
            if not child0:
                continue
            p: Path = child0.data(PATH_ROLE)
            rel = self._rel_posix(p)
            base = (p.name if p else "")
            if base in deny_names or rel in deny_names:
                self.set_explicit_rule(child0, RULE_EXCLUDE)

        self.status.setText("OK: よくある候補に変更禁止を付与しました。")

    def clear_explicit_rules(self):
        # まず保存している明示ルールを全消し（未ロードのノードも含めて完全にクリア）
        self.state.project_rules = {}

        root0 = self.model.item(0, 0)
        if not root0:
            self.status.setText("OK: 明示ルールを全解除しました。")
            return

        def walk(item0):
            # プレースホルダ行はスキップ
            if item0.data(PLACEHOLDER_ROLE):
                return

            item0.setData(RULE_INHERIT, RULE_ROLE)

            # Rule列の表示更新
            rule_item = self.model.itemFromIndex(item0.index().siblingAtColumn(1))
            if rule_item:
                rule_item.setText(rule_label(RULE_INHERIT))

            # 色リセット
            item0.setBackground(QBrush())

            for i in range(item0.rowCount()):
                c0 = item0.child(i, 0)
                if c0:
                    walk(c0)

        walk(root0)

        # 表示上の実効ルール色も更新（メソッドがある場合だけ）
        if hasattr(self, "_refresh_effective_colors"):
            self._refresh_effective_colors(root0)

        self.status.setText("OK: 明示ルールを全解除しました。")

    def _add_placeholder_if_dir(self, item0):
        if not item0.data(IS_DIR_ROLE):
            return
        if item0.data(LOADED_ROLE):
            return
        # 既にプレースホルダがあるなら何もしない
        if item0.rowCount() > 0:
            c = item0.child(0, 0)
            if c and c.data(PLACEHOLDER_ROLE):
                return

        ph0 = self.QStandardItem("…")
        ph1 = self.QStandardItem("")
        ph0.setData(True, PLACEHOLDER_ROLE)
        item0.appendRow([ph0, ph1])

        def clear_explicit_rules(self):
            root0 = self.model.item(0, 0)
            if not root0:
                return

            def walk(item0):
                item0.setData(RULE_INHERIT, RULE_ROLE)
                self._sync_row(item0)
                for i in range(item0.rowCount()):
                    c0 = item0.child(i, 0)
                    if c0:
                        walk(c0)

            walk(root0)
            self._export_to_state()
            self.status.setText("OK: 明示ルールを全解除しました。")


class TitleBar(QWidget):
    def __init__(self, parent: QMainWindow):
        super().__init__(parent)
        self.parent = parent
        self.setFixedHeight(44)

        self._dragging = False
        self._drag_pos = QPoint()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)

        self.title = QLabel("ぴーごの厨房 - AGENTS.md Wizard")
        self.title.setObjectName("TitleLabel")
        layout.addWidget(self.title)
        layout.addStretch()

        self.btn_min = QPushButton("—")
        self.btn_max = QPushButton("□")
        self.btn_close = QPushButton("×")

        self.btn_min.clicked.connect(self.parent.showMinimized)
        self.btn_max.clicked.connect(self.toggle_max_restore)
        self.btn_close.clicked.connect(self.parent.close)

        for b in (self.btn_min, self.btn_max, self.btn_close):
            b.setFixedSize(34, 28)
            layout.addWidget(b)

    def toggle_max_restore(self):
        if self.parent.isMaximized():
            self.parent.showNormal()
        else:
            self.parent.showMaximized()

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return

        # 可能ならOSの「システム移動」
        wh = self.parent.windowHandle()
        if wh and wh.startSystemMove():
            e.accept()
            return

        # フォールバック：手動ドラッグ
        self._dragging = True
        self._drag_pos = e.globalPosition().toPoint() - self.parent.frameGeometry().topLeft()
        e.accept()

    def mouseMoveEvent(self, e):
        if not self._dragging:
            return
        self.parent.move(e.globalPosition().toPoint() - self._drag_pos)
        e.accept()

    def mouseReleaseEvent(self, e):
        self._dragging = False
        e.accept()


class ProjectPage(QFrame):
    """Step 1: project root + meta input"""

    def __init__(self, state: WizardState):
        super().__init__()
        self.state = state

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        title = QLabel("Project")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        lay.addWidget(title)

        desc = QLabel(
            "このプロジェクトの AGENTS.md を作るために、\n"
            "(1) ルートディレクトリ と (2) 方針・目的 を入力します。"
        )
        desc.setStyleSheet("opacity: 0.85;")
        lay.addWidget(desc)

        # ---- project root ----
        root_row = QHBoxLayout()
        self.root_edit = QLineEdit()
        self.root_edit.setReadOnly(True)
        self.root_edit.setPlaceholderText("プロジェクトルートを選択してください")

        self.btn_choose = QPushButton("フォルダを選択")
        self.btn_choose.clicked.connect(self.choose_folder)

        root_row.addWidget(self.root_edit, 1)
        root_row.addWidget(self.btn_choose)
        lay.addLayout(root_row)

        # ---- project name ----
        lay.addWidget(QLabel("プロジェクト名"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("例: haken_job / merumo / client-site")
        self.name_edit.textChanged.connect(self._on_name_changed)
        lay.addWidget(self.name_edit)

        # ---- purpose ----
        lay.addWidget(QLabel("目的 / ゴール"))
        self.purpose_edit = QTextEdit()
        self.purpose_edit.setPlaceholderText(
            "例:\n"
            "- AGENTS.md を生成して、Codex の作業品質を安定させる\n"
            "- 変更禁止領域を明確化して事故を防ぐ\n"
        )
        self.purpose_edit.setMinimumHeight(120)
        self.purpose_edit.textChanged.connect(self._on_purpose_changed)
        lay.addWidget(self.purpose_edit)

        # ---- policy ----
        lay.addWidget(QLabel("方針 / 制約（守ってほしいこと）"))
        self.policy_edit = QTextEdit()
        self.policy_edit.setPlaceholderText(
            "例:\n"
            "- 変更は最小限。既存仕様を崩さない\n"
            "- 変更前にテストを確認し、変更後も必ずテストを通す\n"
            "- vendor/ や node_modules/ は編集しない\n"
        )
        self.policy_edit.setMinimumHeight(150)
        self.policy_edit.textChanged.connect(self._on_policy_changed)
        lay.addWidget(self.policy_edit)

        self.status = QLabel("")
        self.status.setStyleSheet("opacity: 0.85;")
        lay.addWidget(self.status)

        lay.addStretch()

        self._sync_from_state()

    def _sync_from_state(self):
        if self.state.project_root:
            self.root_edit.setText(str(self.state.project_root))
        if self.state.project_name:
            self.name_edit.setText(self.state.project_name)
        if self.state.project_purpose:
            self.purpose_edit.setPlainText(self.state.project_purpose)
        if self.state.project_policy:
            self.policy_edit.setPlainText(self.state.project_policy)

    def _on_name_changed(self, text: str):
        self.state.project_name = text.strip()

    def _on_purpose_changed(self):
        self.state.project_purpose = self.purpose_edit.toPlainText().strip()

    def _on_policy_changed(self):
        self.state.project_policy = self.policy_edit.toPlainText().strip()

    def choose_folder(self):
        start_dir = str(self.state.project_root) if self.state.project_root else str(Path.home())

        path = QFileDialog.getExistingDirectory(
            self,
            "プロジェクトルートを選択",
            start_dir,
        )
        if not path:
            return

        p = Path(path)
        self.state.project_root = p
        self.root_edit.setText(str(p))

        if not self.state.project_name:
            self.name_edit.setText(p.name)

        if (p / ".git").exists():
            self.status.setText("OK: Gitリポジトリを検出しました。")
        else:
            self.status.setText("OK: ルートを選択しました（.git は見つかりませんでした）。")

    def validate(self) -> tuple[bool, str]:
        if self.state.project_root is None:
            return False, "先にプロジェクトルートを選択してください。"
        return True, ""


class FramelessWizardWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        # ★ これが無いと self.state が存在せず落ちます
        self.state = WizardState()

        self.setWindowTitle("ぴーごの厨房")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setMinimumSize(980, 640)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        self.titlebar = TitleBar(self)
        root_layout.addWidget(self.titlebar)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(12, 12, 12, 12)
        content_layout.setSpacing(12)

        center = QWidget()
        center_layout = QHBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(12)

        self.steps = QListWidget()
        self.steps.setFixedWidth(240)
        self.step_titles = ["1. Project", "2. Rules", "3. Workflow", "4. Preview"]
        self.steps.addItems(self.step_titles)
        self.steps.setCurrentRow(0)

        self.pages = QStackedWidget()

        # Step 1
        self.page_project = ProjectPage(self.state)
        self.pages.addWidget(self.page_project)

        # Step 2（置き換え）
        self.page_rules = RulesPage(self.state)
        self.pages.addWidget(self.page_rules)

        # placeholders（Step番号がズレないように注意）
        self.pages.addWidget(self.make_page("Workflow: コマンド登録（次で実装）"))
        self.pages.addWidget(self.make_page("Preview: AGENTS.md生成/差分/保存（次で実装）"))

        self.steps.currentRowChanged.connect(self.on_step_clicked)

        center_layout.addWidget(self.steps)
        center_layout.addWidget(self.pages, 1)

        nav = QWidget()
        nav_layout = QHBoxLayout(nav)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.addStretch()

        self.btn_back = QPushButton("Back")
        self.btn_next = QPushButton("Next")
        self.btn_back.clicked.connect(self.go_back)
        self.btn_next.clicked.connect(self.go_next)

        nav_layout.addWidget(self.btn_back)
        nav_layout.addWidget(self.btn_next)

        content_layout.addWidget(center, 1)
        content_layout.addWidget(nav)

        root_layout.addWidget(content, 1)
        self.setCentralWidget(root)

        self.current_step = 0
        self.goto_step(0)

    def make_page(self, text: str) -> QWidget:
        w = QFrame()
        lay = QVBoxLayout(w)
        label = QLabel(text)
        label.setWordWrap(True)
        lay.addWidget(label)
        lay.addStretch()
        return w

    def on_step_clicked(self, row: int):
        # クリックで飛ばさず Next/Back で進む（将来「完了済みのみ飛べる」にできる）
        if row != self.current_step:
            self.steps.blockSignals(True)
            self.steps.setCurrentRow(self.current_step)
            self.steps.blockSignals(False)

    def goto_step(self, step: int):
        step = max(0, min(step, self.pages.count() - 1))
        self.current_step = step
        self.pages.setCurrentIndex(step)

        self.steps.blockSignals(True)
        self.steps.setCurrentRow(step)
        self.steps.blockSignals(False)

        self.btn_back.setEnabled(step > 0)
        self.btn_next.setText("Finish" if step == self.pages.count() - 1 else "Next")

        # ★ 追加：Step2に入った瞬間にツリーを読み込む
        if step == 1 and hasattr(self, "page_rules"):
            self.page_rules.reload_tree()

    def go_back(self):
        self.goto_step(self.current_step - 1)

    def go_next(self):
        if self.current_step == 0:
            ok, msg = self.page_project.validate()
            if not ok:
                self.page_project.status.setText(msg)
                return

        if self.current_step == self.pages.count() - 1:
            self.close()
            return

        self.goto_step(self.current_step + 1)


if __name__ == "__main__":
    app = QApplication([])

    # 見た目の破綻防止（色は指定しない）
    app.setStyleSheet(
        """
        QLabel#TitleLabel { font-weight: 600; }
        QLineEdit, QTextEdit { padding: 8px; border-radius: 10px; }
        QPushButton { padding: 8px 12px; border-radius: 10px; }
        QListWidget { padding: 6px; border-radius: 12px; }
        """
    )

    win = FramelessWizardWindow()
    win.show()
    app.exec()
