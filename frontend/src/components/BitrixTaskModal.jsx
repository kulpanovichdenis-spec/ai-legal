import React, { useState, useEffect } from 'react';
import { X, ExternalLink, CheckCircle2, AlertTriangle, Loader2 } from 'lucide-react';

// Мини-окно создания задачи в Битрикс24 из чата.
// Поля: Название (предзаполнено) + Описание. Ответственный/соисполнитель
// задаются на бэкенде (оба юриста), выбор в UI не нужен.
export default function BitrixTaskModal({ open, defaultTitle, author, onClose, onSubmit }) {
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [status, setStatus] = useState('form'); // form | sending | done | error
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    if (open) {
      setTitle(defaultTitle || '');
      // Описание предзаполняем постановщиком — пользователь может дописать
      // или исправить ФИО. От этого зависит, не удалят ли задачу.
      setDescription(author ? `Постановщик: ${author}\n\n` : 'Постановщик: ');
      setStatus('form');
      setResult(null);
      setError('');
    }
  }, [open, defaultTitle, author]);

  if (!open) return null;

  const submit = async () => {
    const t = title.trim();
    if (!t) { setError('Введите название задачи'); return; }
    setStatus('sending');
    setError('');
    try {
      const res = await onSubmit({ title: t, description: description.trim() });
      setResult(res);
      setStatus('done');
    } catch (e) {
      setError(e.message || 'Не удалось создать задачу');
      setStatus('error');
    }
  };

  const sending = status === 'sending';

  return (
    <div
      onClick={sending ? undefined : onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 100,
        background: 'rgba(0,0,0,0.4)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        padding: 16,
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: 'var(--bg, #fff)', borderRadius: 10,
          width: '100%', maxWidth: 460, padding: 20,
          boxShadow: '0 12px 40px rgba(0,0,0,0.25)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
          <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700 }}>Задача в Битрикс24</h3>
          <button className="btn btn-ghost btn-sm" onClick={onClose} disabled={sending} title="Закрыть">
            <X size={16} />
          </button>
        </div>

        {status === 'done' ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#16a34a', fontWeight: 600 }}>
              <CheckCircle2 size={18} /> Задача #{result?.task_id} создана
            </div>
            {result && !result.file_attached && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: '#b45309', fontSize: 13 }}>
                <AlertTriangle size={14} /> Задача создана, но файл не удалось прикрепить.
              </div>
            )}
            {result?.url && (
              <a href={result.url} target="_blank" rel="noopener noreferrer"
                 className="btn btn-primary btn-sm" style={{ alignSelf: 'flex-start', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                <ExternalLink size={14} /> Открыть задачу
              </a>
            )}
            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <button className="btn btn-ghost btn-sm" onClick={onClose}>Готово</button>
            </div>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
              <span style={{ fontWeight: 600 }}>Название задачи</span>
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                disabled={sending}
                maxLength={250}
                style={{ padding: '8px 10px', borderRadius: 6, border: '1px solid var(--border, #d1d5db)', fontSize: 14 }}
              />
            </label>
            <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
              <span style={{ fontWeight: 600 }}>Описание и постановщик</span>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                disabled={sending}
                rows={5}
                style={{ padding: '8px 10px', borderRadius: 6, border: '1px solid var(--border, #d1d5db)', fontSize: 14, resize: 'vertical' }}
              />
            </label>
            <div style={{
              fontSize: 12.5, color: '#92400e', background: '#fffbeb',
              border: '1px solid #fde68a', borderRadius: 6, padding: '8px 10px',
              display: 'flex', gap: 6,
            }}>
              <AlertTriangle size={16} style={{ flexShrink: 0, marginTop: 1 }} />
              <span>Опишите задачу и укажите ваше ФИО (постановщика). Если постановщика задачи невозможно будет определить — задача будет удалена.</span>
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-dim, #888)' }}>
              К задаче будет прикреплён Word-файл с историей чата. Ответственные назначаются автоматически.
            </div>
            {error && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: '#dc2626', fontSize: 13 }}>
                <AlertTriangle size={14} /> {error}
              </div>
            )}
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <button className="btn btn-ghost btn-sm" onClick={onClose} disabled={sending}>Отмена</button>
              <button className="btn btn-primary btn-sm" onClick={submit} disabled={sending}
                      style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                {sending ? <><Loader2 size={14} className="spin" /> Создание…</> : 'Создать'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
