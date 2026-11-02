from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPointF,
    QParallelAnimationGroup,
    QPropertyAnimation,
)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QGraphicsDropShadowEffect,
    QLineEdit,
    QPushButton,
    QWidget,
)


class InteractionAnimator(QObject):
    """Adds a subtle lift/press animation to buttons and form controls."""

    _INPUT_TYPES = (QLineEdit, QAbstractSpinBox, QComboBox)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if not isinstance(watched, QWidget):
            return False

        control = watched
        parent = watched.parentWidget()
        if isinstance(watched, QLineEdit) and isinstance(parent, (QAbstractSpinBox, QComboBox)):
            # Spin boxes and editable combo boxes own an internal line edit.
            # Animate the complete control instead of producing a double shadow.
            control = parent

        is_button = isinstance(control, QPushButton)
        is_input = isinstance(control, self._INPUT_TYPES)
        if not is_button and not is_input:
            return False

        event_type = event.type()
        if is_button:
            if event_type == QEvent.Type.Enter and control.isEnabled():
                self._animate(control, blur=13.0, y_offset=2.5, duration=150)
            elif event_type == QEvent.Type.Leave:
                self._animate(control, blur=2.0, y_offset=0.0, duration=170)
            elif event_type == QEvent.Type.MouseButtonPress and control.isEnabled():
                self._animate(control, blur=4.0, y_offset=0.5, duration=75)
            elif event_type == QEvent.Type.MouseButtonRelease and control.isEnabled():
                if control.underMouse():
                    self._animate(control, blur=13.0, y_offset=2.5, duration=120)
                else:
                    self._animate(control, blur=2.0, y_offset=0.0, duration=120)
            elif event_type == QEvent.Type.EnabledChange and not control.isEnabled():
                self._animate(control, blur=0.0, y_offset=0.0, duration=100)
        else:
            if event_type == QEvent.Type.FocusIn:
                self._animate(control, blur=11.0, y_offset=2.0, duration=150)
            elif event_type == QEvent.Type.FocusOut:
                target_blur = 7.0 if control.underMouse() else 1.0
                self._animate(control, blur=target_blur, y_offset=1.0, duration=160)
            elif event_type == QEvent.Type.Enter and not control.hasFocus():
                self._animate(control, blur=7.0, y_offset=1.0, duration=150)
            elif event_type == QEvent.Type.Leave and not control.hasFocus():
                self._animate(control, blur=1.0, y_offset=0.0, duration=170)

        return False

    @staticmethod
    def _animate(widget: QWidget, blur: float, y_offset: float, duration: int) -> None:
        effect = getattr(widget, "_interaction_shadow", None)
        if effect is None:
            existing_effect = widget.graphicsEffect()
            if existing_effect is not None:
                return
            effect = QGraphicsDropShadowEffect(widget)
            effect.setColor(QColor(0, 0, 0, 105))
            effect.setBlurRadius(1.0)
            effect.setOffset(0.0, 0.0)
            widget.setGraphicsEffect(effect)
            widget._interaction_shadow = effect

        previous_group = getattr(widget, "_interaction_animation", None)
        if previous_group is not None:
            previous_group.stop()
            previous_group.deleteLater()

        group = QParallelAnimationGroup(widget)
        blur_animation = QPropertyAnimation(effect, b"blurRadius", group)
        blur_animation.setStartValue(effect.blurRadius())
        blur_animation.setEndValue(blur)
        blur_animation.setDuration(duration)
        blur_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        offset_animation = QPropertyAnimation(effect, b"offset", group)
        offset_animation.setStartValue(effect.offset())
        offset_animation.setEndValue(QPointF(0.0, y_offset))
        offset_animation.setDuration(duration)
        offset_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        widget._interaction_animation = group
        group.start()


def install_interaction_animations(app: QApplication) -> InteractionAnimator:
    animator = InteractionAnimator(app)
    app.installEventFilter(animator)
    return animator
