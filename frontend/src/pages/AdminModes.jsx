import React, { useState, useEffect } from 'react';
import { apiGet, apiPut } from '../api.js';

export default function AdminModes() {
  const [modes, setModes] = useState([]);
  const [selected, setSelected] = useState(null);
  const [instruction, setInstruction] = useState('');
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState('');

  useEffect(() => {
    apiGet('/admin/modes').then(setModes).catch(() => {});
  }, []);

  const handleSelect = (m) => {
    setSelected(m);
    setInstruction(m.instruction);
    setMessage('');
  };

  const handleSave = async () => {
    if (!selected) return;
    setSaving(true);
    setMessage('');
    try {
      const form = new FormData();
      form.append('instruction', instruction);
      await apiPut(`/admin/modes/${selected.mode}`, form);
      setModes(prev => prev.map(m =>
        m.mode === selected.mode ? { ...m, instruction } : m
      ));
      setSelected(prev => ({ ...prev, instruction }));
      setMessage('Сохранено');
    } catch (err) {
      setMessage(`Ошибка: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <div className="main-header">
        <h2>Инструкции режимов</h2>
      </div>

      <div style={{ display: 'flex', gap: 24, padding: '24px', height: 'calc(100% - 65px)', boxSizing: 'border-box' }}>
        <div style={{ width: 220, flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
          {modes.map(m => (
            <button
              key={m.mode}
              onClick={() => handleSelect(m)}
              style={{
                padding: '10px 14px', borderRadius: 8, border: 'none', cursor: 'pointer',
                textAlign: 'left', fontWeight: selected?.mode === m.mode ? 600 : 400,
                background: selected?.mode === m.mode ? 'var(--accent-soft)' : 'var(--surface)',
                color: selected?.mode === m.mode ? 'var(--accent)' : 'var(--text)',
                fontSize: 14,
              }}
            >
              {m.label}
            </button>
          ))}
        </div>

        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 12 }}>
          {selected ? (
            <>
              <div style={{ fontWeight: 600, fontSize: 15 }}>{selected.label}</div>
              <textarea
                value={instruction}
                onChange={e => setInstruction(e.target.value)}
                style={{
                  flex: 1, padding: 14, borderRadius: 8, resize: 'none',
                  background: 'var(--surface)', color: 'var(--text)',
                  border: '1px solid var(--border)', fontSize: 14, lineHeight: 1.6,
                  fontFamily: 'inherit',
                }}
              />
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
                  {saving ? 'Сохранение...' : 'Сохранить'}
                </button>
                {message && (
                  <span style={{ fontSize: 13, color: message.startsWith('Ошибка') ? 'var(--error)' : 'var(--accent)' }}>
                    {message}
                  </span>
                )}
              </div>
            </>
          ) : (
            <div style={{ color: 'var(--text-dim)', fontSize: 14, marginTop: 8 }}>
              Выберите режим для редактирования инструкции
            </div>
          )}
        </div>
      </div>
    </>
  );
}
