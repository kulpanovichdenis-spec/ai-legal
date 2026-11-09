import React, { useState, useEffect } from 'react';
import { apiGet, apiDelete, apiPatch } from '../api.js';
import { Trash2, KeyRound } from 'lucide-react';
import { ConfirmModal } from '../components/Modal.jsx';

const ROLE_LABELS = {
  admin: 'Администратор',
  dashboard: 'Дашборд',
  user: 'Сотрудник',
};

export default function AdminUsers() {
  const [users, setUsers] = useState([]);
  const [pwdModal, setPwdModal] = useState(null); // { id, username }
  const [newPassword, setNewPassword] = useState('');
  const [pwdError, setPwdError] = useState('');
  const [pwdLoading, setPwdLoading] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState(null); // { id, username }

  const load = async () => {
    try { setUsers(await apiGet('/users')); } catch {}
  };

  useEffect(() => { load(); }, []);

  const requestDelete = (user) => {
    setDeleteConfirm({ id: user.id, username: user.username });
  };

  const confirmDelete = async () => {
    if (!deleteConfirm) return;
    try {
      await apiDelete(`/users/${deleteConfirm.id}`);
      await load();
    } catch (err) {
      alert('Не удалось удалить пользователя: ' + err.message);
    }
  };

  const openPwdModal = (user) => {
    setPwdModal({ id: user.id, username: user.username });
    setNewPassword('');
    setPwdError('');
  };

  const closePwdModal = () => {
    setPwdModal(null);
    setNewPassword('');
    setPwdError('');
  };

  const handleChangePassword = async (e) => {
    e.preventDefault();
    if (newPassword.length < 8) {
      setPwdError('Пароль должен быть не менее 8 символов');
      return;
    }
    setPwdLoading(true);
    setPwdError('');
    try {
      await apiPatch(`/users/${pwdModal.id}/password`, { new_password: newPassword });
      closePwdModal();
    } catch (err) {
      setPwdError(err.message || 'Ошибка при смене пароля');
    } finally {
      setPwdLoading(false);
    }
  };

  return (
    <div className="admin-page">
      <h2>Пользователи</h2>

      <div className="admin-card">
        {users.length === 0 ? (
          <p style={{ color: 'var(--text-dim)', fontSize: 13, padding: 16, textAlign: 'center' }}>
            Загрузка...
          </p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Логин</th>
                <th>Роль</th>
                <th>Создан</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {users.map(u => (
                <tr key={u.id}>
                  <td style={{ fontWeight: 600 }}>{u.username}</td>
                  <td>
                    <span className={`badge badge-${u.role}`}>
                      {ROLE_LABELS[u.role] ?? u.role}
                    </span>
                  </td>
                  <td>{new Date(u.created_at).toLocaleDateString('ru-RU')}</td>
                  <td style={{ display: 'flex', gap: 6 }}>
                    {u.role === 'dashboard' && (
                      <button
                        className="btn btn-ghost btn-sm"
                        title="Сменить пароль"
                        onClick={() => openPwdModal(u)}
                      >
                        <KeyRound size={13} />
                      </button>
                    )}
                    {u.username !== 'admin' && u.role !== 'dashboard' && (
                      <button className="btn btn-danger btn-sm" onClick={() => requestDelete(u)}>
                        <Trash2 size={13} />
                      </button>
                    )}
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
        title="Удалить пользователя?"
        message={deleteConfirm ? `Пользователь «${deleteConfirm.username}» будет удалён.` : ''}
        danger
        confirmText="Удалить"
      />

      {pwdModal && (
        <div className="modal-overlay" onClick={closePwdModal}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <h3 style={{ marginBottom: 16 }}>Смена пароля — {pwdModal.username}</h3>
            <form onSubmit={handleChangePassword}>
              <div style={{ marginBottom: 12 }}>
                <label style={{ display: 'block', fontSize: 13, marginBottom: 4, color: 'var(--text-dim)' }}>
                  Новый пароль
                </label>
                <input
                  type="password"
                  className="form-input"
                  value={newPassword}
                  onChange={e => setNewPassword(e.target.value)}
                  placeholder="Не менее 8 символов"
                  autoFocus
                />
              </div>
              {pwdError && (
                <p style={{ color: 'var(--danger)', fontSize: 13, marginBottom: 10 }}>{pwdError}</p>
              )}
              <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                <button type="button" className="btn btn-ghost" onClick={closePwdModal}>
                  Отмена
                </button>
                <button type="submit" className="btn btn-primary" disabled={pwdLoading}>
                  {pwdLoading ? 'Сохранение...' : 'Сохранить'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
