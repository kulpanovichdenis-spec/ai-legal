import React, { useState } from 'react';
import { login, userLogin } from '../api.js';
import { Scale } from 'lucide-react';

export default function LoginPage({ onLogin }) {
  const [tab, setTab] = useState('user');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleTabChange = (newTab) => {
    setTab(newTab);
    setError('');
    setUsername('');
    setPassword('');
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const data = tab === 'admin'
        ? await login({ username, password })
        : await userLogin(username);
      onLogin(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-page">
      <div className="login-card">
        <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 16 }}>
          <div style={{
            width: 48, height: 48, borderRadius: 12,
            background: 'var(--accent-soft)', display: 'flex',
            alignItems: 'center', justifyContent: 'center'
          }}>
            <Scale size={24} color="var(--accent)" />
          </div>
        </div>
        <h1>Юридический AI-ассистент</h1>

        <div style={{ display: 'flex', gap: 8, marginBottom: 24, marginTop: 8 }}>
          <button
            type="button"
            onClick={() => handleTabChange('user')}
            style={{
              flex: 1, padding: '8px 0', borderRadius: 8, border: 'none', cursor: 'pointer',
              fontWeight: 600, fontSize: 14,
              background: tab === 'user' ? 'var(--accent)' : 'var(--surface)',
              color: tab === 'user' ? '#fff' : 'var(--text-dim)',
              transition: 'all 0.15s',
            }}
          >
            Пользователь
          </button>
          <button
            type="button"
            onClick={() => handleTabChange('admin')}
            style={{
              flex: 1, padding: '8px 0', borderRadius: 8, border: 'none', cursor: 'pointer',
              fontWeight: 600, fontSize: 14,
              background: tab === 'admin' ? 'var(--accent)' : 'var(--surface)',
              color: tab === 'admin' ? '#fff' : 'var(--text-dim)',
              transition: 'all 0.15s',
            }}
          >
            Администратор
          </button>
        </div>

        {error && <div className="login-error">{error}</div>}

        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label>{tab === 'admin' ? 'Логин' : 'Ваше имя'}</label>
            <input
              className="form-input"
              type="text"
              value={username}
              onChange={e => setUsername(e.target.value)}
              placeholder={tab === 'admin' ? 'Введите логин' : 'Введите имя'}
              required
              autoComplete="username"
            />
          </div>

          {tab === 'admin' && (
            <div className="form-group">
              <label>Пароль</label>
              <input
                className="form-input"
                type="password"
                value={password}
                onChange={e => setPassword(e.target.value)}
                placeholder="Введите пароль"
                required
                autoComplete="current-password"
              />
            </div>
          )}

          <button className="btn btn-primary" type="submit" disabled={loading}>
            {loading ? 'Загрузка...' : 'Войти'}
          </button>
        </form>

        {tab === 'user' && (
          <p style={{ textAlign: 'center', marginTop: 16, fontSize: 12, color: 'var(--text-dim)', lineHeight: 1.6 }}>
            Введите имя и нажмите «Войти» — пароль не нужен
          </p>
        )}
        {tab === 'admin' && (
          <p style={{ textAlign: 'center', marginTop: 16, fontSize: 12, color: 'var(--text-dim)', lineHeight: 1.6 }}>
            Для получения доступа обратитесь к администратору
          </p>
        )}
      </div>
    </div>
  );
}
