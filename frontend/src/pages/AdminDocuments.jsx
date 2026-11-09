import React, { useState, useEffect, useRef } from 'react';
import { apiGet, apiUpload, apiDelete, apiPatch } from '../api.js';
import { Upload, Trash2, FileText, AlertTriangle, ShieldAlert, Pencil, Check, X } from 'lucide-react';
import { ConfirmModal } from '../components/Modal.jsx';

const CATEGORIES = [
  { value: 'regulation', label: 'Внутренний регламент' },
  { value: 'contract', label: 'Шаблон документа' },
  { value: 'reference', label: 'Справочный материал' },
  { value: 'leaflet', label: 'Листок-вкладыш' },
];

const CHAT_MODES = [
  { value: 'consult', label: 'Консультация' },
  { value: 'ad_check', label: 'Проверка рекламы' },
  { value: 'document_gen', label: 'Документы' },
  { value: 'regulation', label: 'Регламенты' },
];

function parseModes(modeStr) {
  if (!modeStr || modeStr === 'all') return ['all'];
  return modeStr.split(',').map(m => m.trim()).filter(Boolean);
}

function formatModes(modes) {
  if (!modes || modes.length === 0 || modes.includes('all')) return 'Все режимы';
  return modes.map(m => CHAT_MODES.find(cm => cm.value === m)?.label || m).join(', ');
}

function ModesCheckboxes({ selected, onChange }) {
  const allSelected = selected.includes('all');

  const toggleAll = () => {
    onChange(['all']);
  };

  const toggleMode = (value) => {
    let next;
    if (selected.includes(value)) {
      next = selected.filter(m => m !== value && m !== 'all');
      if (next.length === 0) next = ['all'];
    } else {
      next = selected.filter(m => m !== 'all');
      next = [...next, value];
    }
    onChange(next);
  };

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px 16px', marginTop: 4 }}>
      <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: 13 }}>
        <input type="checkbox" checked={allSelected} onChange={toggleAll} />
        Все режимы
      </label>
      {CHAT_MODES.map(m => (
        <label key={m.value} style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: 13 }}>
          <input
            type="checkbox"
            checked={!allSelected && selected.includes(m.value)}
            onChange={() => toggleMode(m.value)}
          />
          {m.label}
        </label>
      ))}
    </div>
  );
}

export default function AdminDocuments() {
  const [docs, setDocs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [category, setCategory] = useState('regulation');
  const [selectedModes, setSelectedModes] = useState(['all']);
  const [description, setDescription] = useState('');
  const [uploadReport, setUploadReport] = useState(null);
  const [editingId, setEditingId] = useState(null);
  const [editModes, setEditModes] = useState(['all']);
  const [savingId, setSavingId] = useState(null);
  const [deleteConfirm, setDeleteConfirm] = useState(null); // { id, name }
  const fileRef = useRef(null);

  const load = async () => {
    try { setDocs(await apiGet('/documents')); } catch {}
  };

  useEffect(() => { load(); }, []);

  const modesFormValue = () =>
    selectedModes.includes('all') ? 'all' : selectedModes.join(',');

  const handleUpload = async (event) => {
    const files = Array.from(event.target.files);
    event.target.value = '';
    if (loading || files.length === 0) return;
    if (files.length > 10) {
      setUploadReport({ errors: ['Выберите не более 10 файлов за раз.'], warnings: [], uploaded: 0 });
      return;
    }

    setLoading(true);
    setUploadReport(null);
    const report = { errors: [], warnings: [], uploaded: 0 };
    const mode = modesFormValue();

    try {
      for (const file of files) {
        const form = new FormData();
        form.append('file', file);
        form.append('category', category);
        form.append('mode', mode);
        form.append('description', description);

        try {
          const result = await apiUpload('/documents/upload', form);
          report.uploaded += 1;
          const markers = Object.entries(result?.pdn_markers ?? {});
          if (markers.length) {
            report.warnings.push({ filename: file.name, markers });
          }
        } catch (error) {
          report.errors.push(file.name + ' — ' + error.message);
        }
      }

      await load();
      if (report.errors.length === 0) setDescription('');
      setUploadReport(report);
    } finally {
      setLoading(false);
    }
  };


  const requestDelete = (doc) => {
    setDeleteConfirm({ id: doc.id, name: doc.original_filename || doc.filename });
  };

  const confirmDelete = async () => {
    if (!deleteConfirm) return;
    try {
      await apiDelete(`/documents/${deleteConfirm.id}`);
      await load();
    } catch (err) {
      alert('Не удалось удалить документ: ' + err.message);
    }
  };

  const startEdit = (doc) => {
    setEditingId(doc.id);
    setEditModes(parseModes(doc.mode));
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditModes(['all']);
  };

  const saveMode = async (id) => {
    setSavingId(id);
    try {
      const modeStr = editModes.includes('all') ? 'all' : editModes.join(',');
      await apiPatch(`/documents/${id}/mode`, { mode: modeStr });
      setDocs(prev => prev.map(d => d.id === id ? { ...d, mode: modeStr } : d));
      setEditingId(null);
    } catch (err) {
      alert('Ошибка сохранения: ' + err.message);
    } finally {
      setSavingId(null);
    }
  };

  return (
    <div className="admin-page">
      <h2>Документы компании</h2>

      <div className="admin-card">
        <h3>Загрузить документ</h3>
        <p style={{ fontSize: 13, color: 'var(--text-dim)', marginBottom: 16 }}>
          Загруженные документы используются AI-ассистентом как контекст для ответов.
          Поддерживаются форматы: PDF, DOCX, XLSX, XLS, TXT, MD, JSONL, JPG, PNG.
        </p>

        {/* D6: предупреждение о псевдоредактировании PDF */}
        <div style={{
          display: 'flex', gap: 10, alignItems: 'flex-start',
          background: 'var(--warning-bg, #fffbe6)', border: '1px solid var(--warning-border, #ffe58f)',
          borderRadius: 8, padding: '10px 14px', marginBottom: 16, fontSize: 13,
          color: 'var(--warning-text, #7d5a00)',
        }}>
          <AlertTriangle size={16} style={{ flexShrink: 0, marginTop: 1 }} />
          <span>
            <strong>PDF-псевдоредактирование:</strong> «зачернённый» текст в PDF может оставаться
            машиночитаемым — AI-ассистент увидит скрытые данные. Перед загрузкой убедитесь,
            что конфиденциальные фрагменты удалены из слоя текста, а не только закрашены поверх.
          </span>
        </div>

        {uploadReport && (
          <div role="status" style={{ marginBottom: 16, fontSize: 13 }}>
            <p>Успешно загружено файлов: {uploadReport.uploaded}.</p>
            {uploadReport.errors.length > 0 && (
              <>
                <p>Ошибки загрузки или индексации. Перед повтором проверьте список документов.</p>
                <ul>
                  {uploadReport.errors.map((error, index) => <li key={index}>{error}</li>)}
                </ul>
              </>
            )}
            {uploadReport.warnings.length > 0 && (
              <>
                <p><ShieldAlert size={14} /> Возможные персональные данные в загруженных файлах.</p>
                <ul>
                  {uploadReport.warnings.map(({ filename, markers }, index) => (
                    <li key={index}>{filename} — {markers.map(([key, value]) => key + ': ' + value).join(', ')}</li>
                  ))}
                </ul>
              </>
            )}
          </div>
        )}


        <div style={{ display: 'flex', gap: 12, marginBottom: 14, flexWrap: 'wrap' }}>
          <div className="form-group" style={{ flex: 1, minWidth: 180, marginBottom: 0 }}>
            <label>Категория</label>
            <select className="form-input" value={category} onChange={e => setCategory(e.target.value)}>
              {CATEGORIES.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
            </select>
          </div>
          <div className="form-group" style={{ flex: 2, minWidth: 220, marginBottom: 0 }}>
            <label>Описание (необязательно)</label>
            <input
              className="form-input"
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="Краткое описание документа"
            />
          </div>
        </div>

        <div className="form-group" style={{ marginBottom: 14 }}>
          <label>Режимы использования</label>
          <ModesCheckboxes selected={selectedModes} onChange={setSelectedModes} />
        </div>

        <div
          className="upload-zone"
          onClick={() => !loading && fileRef.current?.click()}
        >
          <Upload size={24} style={{ marginBottom: 8 }} />
          <div>{loading ? 'Загрузка...' : 'Выберите до 10 файлов (PDF, DOCX, DOC, XLSX, XLS, TXT, MD, JSONL, JPG, PNG)'}</div>
        </div>
        <input ref={fileRef} type="file" hidden multiple disabled={loading} accept=".pdf,.docx,.doc,.txt,.md,.jpg,.jpeg,.png,.xlsx,.xls,.jsonl" onChange={handleUpload} />
      </div>

      <div className="admin-card">
        <h3>Загруженные документы ({docs.length})</h3>
        {docs.length === 0 ? (
          <p style={{ color: 'var(--text-dim)', fontSize: 13, padding: '16px 0' }}>
            Документы ещё не загружены. Добавьте внутренние регламенты, шаблоны договоров
            и справочные материалы.
          </p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Файл</th>
                <th>Категория</th>
                <th>Режим</th>
                <th>Описание</th>
                <th>Дата</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {docs.map(d => (
                <tr key={d.id}>
                  <td style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <FileText size={14} /> {d.original_filename || d.filename}
                  </td>
                  <td>
                    <span className={`badge badge-${d.category}`}>
                      {CATEGORIES.find(c => c.value === d.category)?.label || d.category}
                    </span>
                  </td>
                  <td>
                    {editingId === d.id ? (
                      <div style={{ minWidth: 220 }}>
                        <ModesCheckboxes selected={editModes} onChange={setEditModes} />
                        <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
                          <button
                            className="btn btn-primary btn-sm"
                            onClick={() => saveMode(d.id)}
                            disabled={savingId === d.id}
                          >
                            <Check size={13} /> {savingId === d.id ? '...' : 'Сохранить'}
                          </button>
                          <button className="btn btn-sm" onClick={cancelEdit}>
                            <X size={13} /> Отмена
                          </button>
                        </div>
                      </div>
                    ) : (
                      <span
                        style={{ cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4 }}
                        title="Нажмите для редактирования"
                        onClick={() => startEdit(d)}
                      >
                        {formatModes(parseModes(d.mode))}
                        <Pencil size={12} style={{ opacity: 0.4 }} />
                      </span>
                    )}
                  </td>
                  <td>{d.description || '—'}</td>
                  <td>{new Date(d.created_at).toLocaleDateString('ru-RU')}</td>
                  <td>
                    <button className="btn btn-danger btn-sm" onClick={() => requestDelete(d)}>
                      <Trash2 size={13} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <ConfirmModal
        open={!!deleteConfirm}
        onClose={() => setDeleteConfirm(null)}
        onConfirm={confirmDelete}
        title="Удалить документ?"
        message={deleteConfirm ? `Документ «${deleteConfirm.name}» будет удалён, в т.ч. из поискового индекса.` : ''}
        danger
        confirmText="Удалить"
      />
    </div>
  );
}
