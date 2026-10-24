"""On-demand account group editors; no permanent launcher controls."""

import html

from PyQt6.QtCore import Qt, QSignalBlocker
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QListWidget, QListWidgetItem, QMessageBox,
)

from src.account_groups import (
    DEFAULT_ACCOUNT_GROUPS, get_account_groups, create_group, rename_group, delete_group,
)


def account_group_defaults(ctx):
    return {group: ctx.tl('account_group_' + group) for group in DEFAULT_ACCOUNT_GROUPS}


def show_account_group_name_dialog(ctx, group_id=None):
    defaults = account_group_defaults(ctx)
    groups = get_account_groups(ctx.settings, defaults)
    dlg = QDialog(ctx.window)
    dlg.setObjectName('accountGroupNameDialog')
    dlg.setWindowTitle(ctx.tl('rename_account_group' if group_id else 'new_account_group'))
    layout = QVBoxLayout(dlg)
    layout.addWidget(QLabel(ctx.tl('account_group_name')))
    name = QLineEdit(groups.get(group_id, ''))
    name.setObjectName('accountGroupName')
    layout.addWidget(name)
    error = QLabel()
    error.setTextFormat(Qt.TextFormat.PlainText)
    error.setStyleSheet('color: #D9534F;')
    layout.addWidget(error)
    result = [None]

    def save():
        try:
            if group_id is None:
                result[0] = create_group(ctx.settings, name.text(), defaults)
            else:
                rename_group(ctx.settings, group_id, name.text(), defaults)
                result[0] = group_id
        except ValueError as exc:
            error.setText(ctx.tl(str(exc)))
            return
        dlg.accept()

    buttons = QHBoxLayout()
    for key, callback in (('save_account', save), ('cancel', dlg.reject)):
        button = QPushButton(ctx.tl(key))
        button.setStyleSheet(ctx.btn_style)
        if key == 'save_account':
            button.setObjectName('saveAccountGroup')
        button.clicked.connect(callback)
        buttons.addWidget(button)
    layout.addLayout(buttons)
    name.returnPressed.connect(save)
    try:
        return result[0] if dlg.exec() == QDialog.DialogCode.Accepted else None
    finally:
        dlg.deleteLater()


def show_manage_account_groups(ctx, on_changed):
    defaults = account_group_defaults(ctx)
    dlg = QDialog(ctx.window)
    dlg.setObjectName('accountGroupManager')
    dlg.setWindowTitle(ctx.tl('manage_account_groups'))
    layout = QVBoxLayout(dlg)
    listing = QListWidget()
    listing.setObjectName('managedAccountGroups')
    layout.addWidget(listing)
    hint = QLabel(ctx.tl('account_group_default_hint'))
    hint.setWordWrap(True)
    hint.setStyleSheet('font-weight: normal;')
    layout.addWidget(hint)
    actions = QHBoxLayout()
    rename = QPushButton(ctx.tl('rename_account_group'))
    rename.setObjectName('renameAccountGroup')
    remove = QPushButton(ctx.tl('delete_account_group'))
    remove.setObjectName('deleteAccountGroup')
    for button in (rename, remove):
        button.setStyleSheet(ctx.btn_style)
        actions.addWidget(button)
    layout.addLayout(actions)

    def selected():
        item = listing.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def update_enabled():
        group_id = selected()
        rename.setEnabled(group_id is not None)
        remove.setEnabled(group_id is not None and group_id != 'favorites')

    def refresh(group_id=None):
        with QSignalBlocker(listing):
            listing.clear()
            for key, name in get_account_groups(ctx.settings, defaults).items():
                item = QListWidgetItem(name)
                item.setData(Qt.ItemDataRole.UserRole, key)
                listing.addItem(item)
                if key == (group_id or 'favorites'):
                    listing.setCurrentItem(item)
        update_enabled()

    def rename_selected():
        group_id = selected()
        if group_id and show_account_group_name_dialog(ctx, group_id):
            refresh(group_id)
            on_changed()

    def delete_selected():
        group_id = selected()
        if group_id is None or group_id == 'favorites':
            return
        groups = get_account_groups(ctx.settings, defaults)
        saved = ctx.settings.get_setting('account_groups')
        count = sum(group == group_id for group in saved.values()) if isinstance(saved, dict) else 0
        if count:
            message = ctx.tl('delete_account_group_confirm').format(groups[group_id], count, groups['favorites'])
            if QMessageBox.question(
                dlg, ctx.tl('delete_account_group'),
                '<p>' + html.escape(message).replace('\n', '<br>') + '</p>',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            ) != QMessageBox.StandardButton.Yes:
                return
        delete_group(ctx.settings, group_id, defaults)
        refresh()
        on_changed()

    listing.currentRowChanged.connect(update_enabled)
    rename.clicked.connect(rename_selected)
    remove.clicked.connect(delete_selected)
    close = QPushButton(ctx.tl('close'))
    close.setStyleSheet(ctx.btn_style)
    close.clicked.connect(dlg.accept)
    layout.addWidget(close)
    refresh()
    dlg.resize(320, 300)
    try:
        dlg.exec()
    finally:
        dlg.deleteLater()
