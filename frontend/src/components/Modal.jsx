import React, { useEffect, useState, useRef } from 'react';
import { X } from 'lucide-react';

/**
 * Базовый модал. Закрывается по Esc и клику на оверлей.
 */
export function Modal({ open, onClose, title, children, footer, width = 480 }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === 'Escape') onClose?.(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal"
        style={{ width }}
        onClick={e => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
      >
        {title && (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
            <h3 style={{ margin: 0 }}>{title}</h3>
            <button
              type="button"
              onClick={onClose}
              style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-dim)', padding: 4 }}
              aria-label="Закрыть"
            >
              <X size={18} />
            </button>
          </div>
        )}
        <div>{children}</div>
        {footer && <div className="modal-actions">{footer}</div>}
      </div>
    </div>
  );
}

/**
 * Подтверждение действия (замена window.confirm).
 */
export function ConfirmModal({
  open, onClose, onConfirm,
  title = 'Подтверждение',
  message,
  confirmText = 'Удалить',
  cancelText = 'Отмена',
  danger = false,
}) {
  const handleConfirm = () => {
    onConfirm?.();
    onClose?.();
  };
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      footer={
        <>
          <button type="button" className="btn-secondary" onClick={onClose}>{cancelText}</button>
          <button
            type="button"
            className={danger ? 'btn-danger' : 'btn-primary'}
            onClick={handleConfirm}
            autoFocus
          >
            {confirmText}
          </button>
        </>
      }
    >
      <p style={{ margin: 0, color: 'var(--text)', lineHeight: 1.5 }}>{message}</p>
    </Modal>
  );
}

/**
 * Запрос строкового значения у пользователя (замена window.prompt).
 */
export function PromptModal({
  open, onClose, onSubmit,
  title = 'Ввод значения',
  label,
  defaultValue = '',
  submitText = 'Сохранить',
  cancelText = 'Отмена',
  placeholder = '',
}) {
  const [value, setValue] = useState(defaultValue);
  const inputRef = useRef(null);

  useEffect(() => {
    if (open) {
      setValue(defaultValue);
      // autoFocus в input через ref — даём React время отрисовать
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open, defaultValue]);

  const handleSubmit = () => {
    const trimmed = value.trim();
    if (!trimmed) return;
    onSubmit?.(trimmed);
    onClose?.();
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      footer={
        <>
          <button type="button" className="btn-secondary" onClick={onClose}>{cancelText}</button>
          <button type="button" className="btn-primary" onClick={handleSubmit}>{submitText}</button>
        </>
      }
    >
      {label && (
        <label style={{ display: 'block', marginBottom: 8, fontSize: 13, color: 'var(--text-dim)' }}>
          {label}
        </label>
      )}
      <input
        ref={inputRef}
        type="text"
        value={value}
        placeholder={placeholder}
        onChange={e => setValue(e.target.value)}
        onKeyDown={e => {
          if (e.key === 'Enter') {
            e.preventDefault();
            handleSubmit();
          }
        }}
        style={{
          width: '100%',
          padding: '10px 12px',
          border: '1px solid var(--border)',
          borderRadius: 8,
          fontSize: 14,
          outline: 'none',
        }}
      />
    </Modal>
  );
}
