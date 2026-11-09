import React, { useState, useEffect } from 'react';
import { apiGet, apiPatch, apiDelete } from '../api.js';
import { Briefcase, Pencil, Trash2, Eye, EyeOff, Check, X, ExternalLink, RotateCcw } from 'lucide-react';
import { ConfirmModal } from '../components/Modal.jsx';

const STATUS_LABELS = {
  published: 'Опубликован',
  archived: 'В архиве',
  draft: 'Черновик',
};

const SLIDE_FIELDS = [
  { key: 'slide_task',       label: 'Слайд 1 — Задача' },
  { key: 'slide_complexity', label: 'Слайд 2 — Сложность' },
  { key: 'slide_action',     label: 'Слайд 3 — Что сделано' },
  { key: 'slide_effect',     label: 'Слайд 4 — Эффект' },
  { key: 'slide_next',       label: 'Слайд 5 — Дальше (опционально)' },
];

function fmtDate(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleDateString('ru-RU');
}

export default function AdminProjects() {
  const [projects, setProjects] = useState([]);
  const [editingId, setEditingId] = useState(null);
  const [editDraft, setEditDraft] = useState({});
  const [savingId, setSavingId] = useState(null);
  const [deleteConfirm, setDeleteConfirm] = useState(null);

  const load = async () => {
    try { setProjects(await apiGet('/admin/projects')); } catch (e) { alert(String(e)); }
  };
  useEffect(() => { load(); }, []);

  const startEdit = (p) => {
    setEditingId(p.id);
    setEditDraft({
      title: p.title,
      slide_task: p.slide_task || '',
      slide_complexity: p.slide_complexity || '',
      slide_action: p.slide_action || '',
      slide_effect: p.slide_effect || '',
      slide_next: p.slide_next || '',
    });
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditDraft({});
  };

  const saveEdit = async (id) => {
    setSavingId(id);
    try {
      await apiPatch(`/admin/projects/${id}`, editDraft);
      await load();
      setEditingId(null);
      setEditDraft({});
    } catch (e) {
      alert('Не удалось сохранить: ' + e.message);
    } finally {
      setSavingId(null);
    }
  };

  const setStatus = async (id, status) => {
    try {
      await apiPatch(`/admin/projects/${id}`, { status });
      await load();
    } catch (e) {
      alert('Не удалось изменить статус: ' + e.message);
    }
  };

  const requestDelete = (p) => {
    setDeleteConfirm({ id: p.id, title: p.title });
  };

  const confirmDelete = async () => {
    if (!deleteConfirm) return;
    try {
      await apiDelete(`/admin/projects/${deleteConfirm.id}`);
      await load();
    } catch (e) {
      alert('Не удалось удалить: ' + e.message);
    }
  };

  return (
    <div className="admin-page">
      <h2>Проекты на дашборде</h2>
      <p style={{ fontSize: 13, color: 'var(--text-muted)', marginBottom: 16 }}>
        Карточки создаются автоматически, когда юрист в режиме «Презентация для бизнеса»
        отвечает на опросник и пишет команду «опубликовать». Здесь — управление
        существующими карточками: редактирование, снятие с публикации (в архив),
        возврат в публикацию, удаление.
      </p>

      <div className="admin-card">
        <h3>Список ({projects.length})</h3>
        {projects.length === 0 ? (
          <p style={{ color: 'var(--text-muted)', padding: '16px 0', fontSize: 13 }}>
            Пока ни одного проекта не опубликовано.
          </p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Проект</th>
                <th>Автор</th>
                <th>Статус</th>
                <th>Создан</th>
                <th>Обновлён</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {projects.map(p => (
                <React.Fragment key={p.id}>
                  <tr>
                    <td>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <Briefcase size={14} />
                        <span>{p.title}</span>
                      </div>
                      <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
                        /projects/{p.slug}
                      </div>
                    </td>
                    <td>{p.author_name || '—'}</td>
                    <td>
                      <span className={`badge badge-${p.status}`}>
                        {STATUS_LABELS[p.status] || p.status}
                      </span>
                    </td>
                    <td>{fmtDate(p.created_at)}</td>
                    <td>{fmtDate(p.updated_at)}</td>
                    <td style={{ whiteSpace: 'nowrap' }}>
                      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                        {p.status === 'published' && (
                          <a
                            href={`/projects/${p.slug}`}
                            target="_blank"
                            rel="noreferrer"
                            className="btn btn-sm"
                            title="Открыть публичную страницу"
                          >
                            <ExternalLink size={13} />
                          </a>
                        )}
                        <button
                          className="btn btn-sm"
                          onClick={() => startEdit(p)}
                          title="Редактировать"
                        >
                          <Pencil size={13} />
                        </button>
                        {p.status === 'published' ? (
                          <button
                            className="btn btn-sm"
                            onClick={() => setStatus(p.id, 'archived')}
                            title="В архив (снять с публикации)"
                          >
                            <EyeOff size={13} />
                          </button>
                        ) : (
                          <button
                            className="btn btn-sm"
                            onClick={() => setStatus(p.id, 'published')}
                            title="Опубликовать"
                          >
                            <Eye size={13} />
                          </button>
                        )}
                        <button
                          className="btn btn-danger btn-sm"
                          onClick={() => requestDelete(p)}
                          title="Удалить"
                        >
                          <Trash2 size={13} />
                        </button>
                      </div>
                    </td>
                  </tr>
                  {editingId === p.id && (
                    <tr>
                      <td colSpan={6} style={{ background: 'var(--sky-soft, #f3f7fb)' }}>
                        <div style={{ padding: 16 }}>
                          <div className="form-group" style={{ marginBottom: 12 }}>
                            <label>Заголовок проекта</label>
                            <input
                              className="form-input"
                              value={editDraft.title}
                              onChange={e => setEditDraft({ ...editDraft, title: e.target.value })}
                            />
                          </div>
                          {SLIDE_FIELDS.map(s => (
                            <div className="form-group" key={s.key} style={{ marginBottom: 12 }}>
                              <label>{s.label}</label>
                              <textarea
                                className="form-input"
                                rows={4}
                                value={editDraft[s.key]}
                                onChange={e => setEditDraft({ ...editDraft, [s.key]: e.target.value })}
                              />
                            </div>
                          ))}
                          <div style={{ display: 'flex', gap: 8 }}>
                            <button
                              className="btn btn-primary"
                              onClick={() => saveEdit(p.id)}
                              disabled={savingId === p.id}
                            >
                              <Check size={14} /> {savingId === p.id ? 'Сохранение…' : 'Сохранить'}
                            </button>
                            <button className="btn" onClick={cancelEdit}>
                              <X size={14} /> Отмена
                            </button>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <ConfirmModal
        open={!!deleteConfirm}
        onClose={() => setDeleteConfirm(null)}
        onConfirm={confirmDelete}
        title="Удалить проект?"
        message={deleteConfirm
          ? `Проект «${deleteConfirm.title}» будет удалён без возможности восстановления. Чтобы временно скрыть — используйте "В архив".`
          : ''}
        danger
        confirmText="Удалить"
      />
    </div>
  );
}
