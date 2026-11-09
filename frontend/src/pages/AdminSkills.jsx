import React, { useState, useEffect, useRef } from 'react';
import { apiGet, apiUpload, apiPut, apiDelete, uploadSkillFile } from '../api.js';
import { Plus, Edit3, Trash2, Save, X, Upload } from 'lucide-react';
import { ConfirmModal } from '../components/Modal.jsx';

const MODES = [
  { value: 'all', label: 'Все режимы' },
  { value: 'consult', label: 'Консультация' },
  { value: 'ad_check', label: 'Проверка рекламы' },
  { value: 'document_gen', label: 'Документы' },
  { value: 'regulation', label: 'Регламенты' },
];

const TIME_CATEGORIES = [
  { value: '', label: 'Не задано (по умолчанию режима)' },
  { value: 'requisites', label: 'Реквизиты / документы контрагента — 10 мин' },
  { value: 'forms', label: 'Типовые формы — 15 мин' },
  { value: 'summary', label: 'Саммари по договору — 120 мин' },
];

const SCENARIOS = [
  { id: 'legal_consultation',     label: 'Юридическая консультация' },
  { id: 'regulation_lookup',      label: 'Поиск по регламентам' },
  { id: 'ad_check_general',       label: 'Проверка рекламы' },
  { id: 'contract_analysis',      label: 'Анализ договора' },
  { id: 'claim_drafting',         label: 'Претензия' },
  { id: 'notice_drafting',        label: 'Уведомление / письмо' },
  { id: 'document_from_contract', label: 'Документ из договора' },
  { id: 'template_form_navigation', label: 'Навигатор по формам' },
  { id: 'consumer_return_claim',  label: 'Возврат товара' },
];

const EMPTY_FORM = {
  name: '', content: '', mode: 'all', time_category: '',
  scenario_ids: [],
  activation_type: 'always',
  activation_keywords: '',
  priority: 100,
};

function parseSkillForm(skill) {
  let scenario_ids = [];
  try { scenario_ids = JSON.parse(skill.scenario_ids || '[]') || []; } catch {}

  let activation_type = 'always';
  let activation_keywords = '';
  const ac = skill.activation_condition || 'always';
  if (ac === 'disabled') {
    activation_type = 'disabled';
  } else if (ac.startsWith('keyword:')) {
    activation_type = 'keyword';
    activation_keywords = ac.slice('keyword:'.length);
  }

  return {
    name: skill.name,
    content: skill.content,
    mode: skill.mode || 'all',
    time_category: skill.time_category || '',
    scenario_ids,
    activation_type,
    activation_keywords,
    priority: skill.priority ?? 100,
  };
}

function activationLabel(skill) {
  const ac = skill.activation_condition || 'always';
  if (ac === 'disabled') return { text: 'Отключён', color: 'var(--error)' };
  if (ac.startsWith('keyword:')) return { text: 'Ключевые слова', color: 'var(--warning, #e6a817)' };
  return null;
}

export default function AdminSkills() {
  const [skills, setSkills] = useState([]);
  const [editing, setEditing] = useState(null);
  const [form, setForm] = useState(EMPTY_FORM);
  const fileInputRef = useRef(null);
  const [deleteConfirm, setDeleteConfirm] = useState(null); // { id, name }

  const load = async () => {
    try { setSkills(await apiGet('/skills')); } catch {}
  };

  useEffect(() => { load(); }, []);

  const handleSave = async () => {
    const fd = new FormData();
    fd.append('name', form.name);
    fd.append('content', form.content);
    fd.append('mode', form.mode);
    fd.append('time_category', form.time_category || '');
    fd.append('scenario_ids', JSON.stringify(form.scenario_ids));
    const ac = form.activation_type === 'keyword'
      ? `keyword:${form.activation_keywords}`
      : form.activation_type;
    fd.append('activation_condition', ac);
    fd.append('priority', String(form.priority));

    try {
      if (editing === 'new') {
        await apiUpload('/skills', fd);
      } else {
        await apiPut(`/skills/${editing.id}`, fd);
      }
      setEditing(null);
      setForm(EMPTY_FORM);
      await load();
    } catch (err) {
      alert('Ошибка: ' + err.message);
    }
  };

  const requestDelete = (skill) => setDeleteConfirm({ id: skill.id, name: skill.name });

  const confirmDelete = async () => {
    if (!deleteConfirm) return;
    try {
      await apiDelete(`/skills/${deleteConfirm.id}`);
      await load();
    } catch (err) {
      alert('Не удалось удалить скилл: ' + err.message);
    }
  };

  const startEdit = (skill) => {
    setEditing(skill);
    setForm(parseSkillForm(skill));
  };

  const startNew = () => {
    setEditing('new');
    setForm(EMPTY_FORM);
  };

  const handleFileUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      await uploadSkillFile(file);
      await load();
    } catch (err) {
      alert('Ошибка загрузки: ' + err.message);
    } finally {
      e.target.value = '';
    }
  };

  const toggleScenario = (id) => {
    setForm(f => ({
      ...f,
      scenario_ids: f.scenario_ids.includes(id)
        ? f.scenario_ids.filter(x => x !== id)
        : [...f.scenario_ids, id],
    }));
  };

  return (
    <div className="admin-page">
      <h2 style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        Скиллы (инструкции для AI)
        <div style={{ display: 'flex', gap: 8 }}>
          <input
            ref={fileInputRef}
            type="file"
            accept=".skill"
            style={{ display: 'none' }}
            onChange={handleFileUpload}
          />
          <button className="btn btn-ghost btn-sm" onClick={() => fileInputRef.current?.click()} title="Загрузить .skill файл">
            <Upload size={14} /> Загрузить .skill
          </button>
          <button className="btn btn-primary btn-sm" onClick={startNew}>
            <Plus size={14} /> Добавить
          </button>
        </div>
      </h2>

      <div className="admin-card" style={{ marginBottom: 20 }}>
        <p style={{ fontSize: 13, color: 'var(--text-dim)', lineHeight: 1.7 }}>
          Скиллы — инструкции, которые добавляются в системный промпт.
          Используйте <strong>Сценарии</strong> для точной привязки скилла к нужным режимам.
          Поле <strong>Условие активации</strong> позволяет включать скилл только при наличии
          ключевых слов в запросе. <strong>Приоритет</strong> определяет порядок при сборке промпта.
        </p>
      </div>

      {/* Edit / Create modal */}
      {editing && (
        <div className="modal-overlay" onClick={() => setEditing(null)}>
          <div className="modal" style={{ maxWidth: 680 }} onClick={e => e.stopPropagation()}>
            <h3>{editing === 'new' ? 'Новый скилл' : `Редактирование: ${editing.name}`}</h3>

            <div className="form-group">
              <label>Название</label>
              <input
                className="form-input"
                value={form.name}
                onChange={e => setForm({ ...form, name: e.target.value })}
                placeholder="Например: Проверка рекламы ЛС"
              />
            </div>

            {/* Scenarios multi-select */}
            <div className="form-group">
              <label>Сценарии</label>
              <div style={{
                display: 'flex', flexWrap: 'wrap', gap: 8,
                padding: '10px 12px',
                background: 'var(--bg)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-sm)',
              }}>
                {SCENARIOS.map(sc => (
                  <label key={sc.id} style={{ display: 'flex', alignItems: 'center', gap: 5, cursor: 'pointer', fontSize: 13 }}>
                    <input
                      type="checkbox"
                      checked={form.scenario_ids.includes(sc.id)}
                      onChange={() => toggleScenario(sc.id)}
                    />
                    {sc.label}
                  </label>
                ))}
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 4 }}>
                Если сценарии не выбраны — скилл активируется на основе поля «Режим» ниже (устаревшее поведение).
              </div>
            </div>

            {/* Activation condition */}
            <div className="form-group">
              <label>Условие активации</label>
              <select
                className="form-input"
                value={form.activation_type}
                onChange={e => setForm({ ...form, activation_type: e.target.value })}
              >
                <option value="always">Всегда</option>
                <option value="keyword">Только при наличии ключевых слов</option>
                <option value="disabled">Отключён</option>
              </select>
              {form.activation_type === 'keyword' && (
                <input
                  className="form-input"
                  style={{ marginTop: 6 }}
                  value={form.activation_keywords}
                  onChange={e => setForm({ ...form, activation_keywords: e.target.value })}
                  placeholder="Ключевые слова через | — например: претензия|заявление|иск"
                />
              )}
              <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 4 }}>
                «Ключевые слова»: скилл активируется только если хотя бы одно слово встречается в сообщении пользователя.
              </div>
            </div>

            {/* Priority */}
            <div className="form-group">
              <label>Приоритет</label>
              <input
                className="form-input"
                type="number"
                min={1} max={999}
                value={form.priority}
                onChange={e => setForm({ ...form, priority: Number(e.target.value) || 100 })}
                style={{ width: 120 }}
              />
              <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 4 }}>
                Меньше = выше в промпте (1 — самый первый). По умолчанию: 100.
              </div>
            </div>

            <div className="form-group">
              <label>Режим (устаревшее — используйте Сценарии)</label>
              <select className="form-input" value={form.mode} onChange={e => setForm({ ...form, mode: e.target.value })}>
                {MODES.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
              </select>
              <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 4 }}>
                Используется только если «Сценарии» не выбраны. Оставьте «Все режимы» при наличии сценариев.
              </div>
            </div>

            <div className="form-group">
              <label>Категория для статистики</label>
              <select className="form-input" value={form.time_category} onChange={e => setForm({ ...form, time_category: e.target.value })}>
                {TIME_CATEGORIES.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
              </select>
            </div>

            <div className="form-group">
              <label>Инструкция (Markdown)</label>
              <textarea
                className="form-input"
                value={form.content}
                onChange={e => setForm({ ...form, content: e.target.value })}
                placeholder="Опишите, как AI должен действовать..."
                style={{ minHeight: 280 }}
              />
            </div>

            <div className="modal-actions">
              <button className="btn btn-ghost" onClick={() => setEditing(null)}>
                <X size={14} /> Отмена
              </button>
              <button className="btn btn-primary" onClick={handleSave} disabled={!form.name || !form.content}>
                <Save size={14} /> Сохранить
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Skills list */}
      {skills.length === 0 ? (
        <div className="admin-card">
          <p style={{ color: 'var(--text-dim)', fontSize: 13, padding: '16px 0', textAlign: 'center' }}>
            Скиллы ещё не добавлены. Создайте первый скилл — например, инструкцию
            по проверке рекламных креативов.
          </p>
        </div>
      ) : (
        skills.map(s => {
          let scenarioNames = [];
          try { scenarioNames = (JSON.parse(s.scenario_ids || '[]') || []).map(id => SCENARIOS.find(sc => sc.id === id)?.label || id); } catch {}
          const badge = activationLabel(s);

          return (
            <div className="admin-card" key={s.id}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <div>
                  <h3 style={{ marginBottom: 4, display: 'flex', alignItems: 'center', gap: 8 }}>
                    {s.name}
                    {badge && (
                      <span style={{
                        fontSize: 11, fontWeight: 600, padding: '2px 7px',
                        background: badge.color + '22', color: badge.color,
                        borderRadius: 4,
                      }}>{badge.text}</span>
                    )}
                    <span style={{ fontSize: 11, color: 'var(--text-dim)', fontWeight: 400 }}>
                      приоритет: {s.priority ?? 100}
                    </span>
                  </h3>
                  <p style={{ fontSize: 12, color: 'var(--text-dim)' }}>
                    {scenarioNames.length > 0
                      ? scenarioNames.join(' · ')
                      : (MODES.find(m => m.value === s.mode)?.label || 'Все режимы')}
                    {s.time_category && (
                      <> · <span style={{ color: 'var(--accent)' }}>{TIME_CATEGORIES.find(c => c.value === s.time_category)?.label || s.time_category}</span></>
                    )}
                    {' · '}
                    Обновлено: {new Date(s.updated_at).toLocaleString('ru-RU')}
                    {' · '}
                    {s.content.length} символов
                  </p>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button className="btn btn-ghost btn-sm" onClick={() => startEdit(s)}>
                    <Edit3 size={13} /> Изменить
                  </button>
                  <button className="btn btn-danger btn-sm" onClick={() => requestDelete(s)}>
                    <Trash2 size={13} />
                  </button>
                </div>
              </div>
              <pre style={{
                marginTop: 12,
                padding: 14,
                background: 'var(--bg)',
                borderRadius: 'var(--radius-sm)',
                fontSize: 12,
                fontFamily: 'var(--font-mono)',
                color: 'var(--text-muted)',
                maxHeight: 200,
                overflow: 'auto',
                whiteSpace: 'pre-wrap',
                lineHeight: 1.6,
              }}>
                {s.content.slice(0, 600)}{s.content.length > 600 ? '\n...' : ''}
              </pre>
            </div>
          );
        })
      )}

      <ConfirmModal
        open={!!deleteConfirm}
        onClose={() => setDeleteConfirm(null)}
        onConfirm={confirmDelete}
        title="Удалить скилл?"
        message={deleteConfirm ? `Скилл «${deleteConfirm.name}» будет удалён.` : ''}
        danger
        confirmText="Удалить"
      />
    </div>
  );
}
