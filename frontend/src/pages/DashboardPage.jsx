import React, { useState, useEffect, useCallback } from 'react';
import {
  BarChart, Bar, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell,
} from 'recharts';
import { Scale, LogOut, RefreshCw, Clock, Users, Activity } from 'lucide-react';
import { apiGet } from '../api.js';

const PERIODS = [
  { id: 'week',  label: 'Неделя' },
  { id: 'month', label: 'Месяц' },
  { id: 'year',  label: 'Год' },
  { id: 'all',   label: 'Всё время' },
];

const MODE_COLORS = {
  consult:      '#1e6fc0',
  ad_check:     '#e67e22',
  document_gen: '#1a8a53',
  regulation:   '#2aa6d6',
};

const SUB_COLORS = {
  summary:    '#0b2a5b',
  claim:      '#1a8a53',
  forms:      '#2aa6d6',
  requisites: '#e67e22',
};

const CHART_COLORS = [
  '#1e6fc0', '#1a8a53', '#2aa6d6', '#e67e22',
  '#9b59b6', '#c0392b', '#16a085', '#d35400',
];

function MetricCard({ icon: Icon, label, value, sub, color }) {
  return (
    <div style={{
      background: 'var(--bg-surface)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius)',
      padding: '20px 24px',
      display: 'flex',
      flexDirection: 'column',
      gap: 6,
      boxShadow: 'var(--shadow)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: color || 'var(--text-dim)', fontSize: 13 }}>
        <Icon size={16} />
        {label}
      </div>
      <div style={{ fontSize: 32, fontWeight: 700, color: 'var(--text)', lineHeight: 1.1 }}>{value}</div>
      {sub && <div style={{ fontSize: 12, color: 'var(--text-dim)' }}>{sub}</div>}
    </div>
  );
}

function SectionTitle({ children }) {
  return (
    <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 12 }}>
      {children}
    </div>
  );
}

function ChartCard({ title, children, style }) {
  return (
    <div style={{
      background: 'var(--bg-surface)',
      border: '1px solid var(--border)',
      borderRadius: 'var(--radius)',
      padding: '20px 24px',
      boxShadow: 'var(--shadow)',
      ...style,
    }}>
      <SectionTitle>{title}</SectionTitle>
      {children}
    </div>
  );
}

const CustomTooltipBar = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 6, padding: '8px 12px', fontSize: 13 }}>
      <div style={{ fontWeight: 600, marginBottom: 4 }}>{label}</div>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color }}>
          {p.name === 'count' ? 'Запросов' : p.name === 'minutes' ? 'Минут' : p.name}: {p.value}
        </div>
      ))}
    </div>
  );
};

export default function DashboardPage({ user, onLogout }) {
  const [period, setPeriod] = useState('month');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [useCustom, setUseCustom] = useState(false);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      let url = `/dashboard/stats?period=${period}`;
      if (useCustom && dateFrom && dateTo) {
        url = `/dashboard/stats?date_from=${dateFrom}&date_to=${dateTo}`;
      }
      setStats(await apiGet(url));
    } catch (e) {
      setError(e.message || 'Ошибка загрузки данных');
    } finally {
      setLoading(false);
    }
  }, [period, useCustom, dateFrom, dateTo]);

  useEffect(() => { load(); }, [load]);

  const handleLogout = async () => {
    try { await apiGet('/auth/logout'); } catch {}
    onLogout();
  };

  const applyCustom = () => {
    if (!dateFrom || !dateTo) return;
    setUseCustom(true);
  };

  const selectPeriod = (id) => {
    setUseCustom(false);
    setPeriod(id);
  };

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg)', display: 'flex', flexDirection: 'column' }}>

      {/* Header */}
      <header style={{
        background: 'var(--navy)',
        borderBottom: '3px solid var(--cyan)',
        padding: '0 32px',
        height: 60,
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        flexShrink: 0,
      }}>
        <Scale size={18} color="white" />
        <span style={{ color: 'white', fontWeight: 700, fontSize: 15, letterSpacing: 0.2 }}>Юрист AI</span>
        <span style={{ color: 'rgba(255,255,255,0.4)', margin: '0 4px' }}>—</span>
        <span style={{ color: 'rgba(255,255,255,0.75)', fontSize: 14 }}>Панель аналитики</span>
        <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 16 }}>
          <button
            onClick={load}
            disabled={loading}
            style={{ background: 'none', border: 'none', color: 'rgba(255,255,255,0.6)', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}
          >
            <RefreshCw size={14} style={{ animation: loading ? 'spin 1s linear infinite' : 'none' }} />
            Обновить
          </button>
          <div style={{ color: 'rgba(255,255,255,0.5)', fontSize: 13 }}>{user.username}</div>
          <button
            onClick={handleLogout}
            style={{ background: 'none', border: 'none', color: 'rgba(255,255,255,0.6)', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}
          >
            <LogOut size={14} />
            Выйти
          </button>
        </div>
      </header>

      {/* Filters */}
      <div style={{
        background: 'var(--bg-surface)',
        borderBottom: '1px solid var(--border)',
        padding: '12px 32px',
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        flexWrap: 'wrap',
      }}>
        {PERIODS.map(p => (
          <button
            key={p.id}
            onClick={() => selectPeriod(p.id)}
            style={{
              padding: '6px 14px',
              borderRadius: 6,
              border: '1px solid',
              borderColor: (!useCustom && period === p.id) ? 'var(--accent)' : 'var(--border)',
              background: (!useCustom && period === p.id) ? 'var(--accent-soft)' : 'transparent',
              color: (!useCustom && period === p.id) ? 'var(--accent)' : 'var(--text-muted)',
              fontWeight: (!useCustom && period === p.id) ? 600 : 400,
              cursor: 'pointer',
              fontSize: 13,
              transition: 'all .15s',
            }}
          >
            {p.label}
          </button>
        ))}

        <div style={{ width: 1, height: 24, background: 'var(--border)', margin: '0 4px' }} />

        <input
          type="date"
          value={dateFrom}
          onChange={e => setDateFrom(e.target.value)}
          style={{ padding: '5px 10px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, color: 'var(--text)', background: 'var(--bg)' }}
        />
        <span style={{ color: 'var(--text-dim)', fontSize: 13 }}>—</span>
        <input
          type="date"
          value={dateTo}
          onChange={e => setDateTo(e.target.value)}
          style={{ padding: '5px 10px', borderRadius: 6, border: '1px solid var(--border)', fontSize: 13, color: 'var(--text)', background: 'var(--bg)' }}
        />
        <button
          onClick={applyCustom}
          disabled={!dateFrom || !dateTo}
          style={{
            padding: '6px 14px',
            borderRadius: 6,
            border: '1px solid',
            borderColor: useCustom ? 'var(--accent)' : 'var(--border)',
            background: useCustom ? 'var(--accent-soft)' : 'var(--bg-elevated)',
            color: useCustom ? 'var(--accent)' : 'var(--text-muted)',
            fontWeight: useCustom ? 600 : 400,
            cursor: dateFrom && dateTo ? 'pointer' : 'not-allowed',
            fontSize: 13,
            opacity: (!dateFrom || !dateTo) ? 0.5 : 1,
          }}
        >
          Применить
        </button>
      </div>

      {/* Body */}
      <div style={{ flex: 1, padding: '28px 32px', overflow: 'auto' }}>
        {error && (
          <div style={{ background: 'var(--danger-soft)', color: 'var(--danger)', border: '1px solid var(--danger)', borderRadius: 8, padding: '12px 16px', marginBottom: 20, fontSize: 13 }}>
            {error}
          </div>
        )}

        {!stats && !error && (
          <div style={{ textAlign: 'center', color: 'var(--text-dim)', paddingTop: 60, fontSize: 14 }}>
            Загрузка данных…
          </div>
        )}

        {stats && (
          <>
            {/* Metric cards */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 16, marginBottom: 24 }}>
              <MetricCard
                icon={Activity}
                label="Всего запросов"
                value={stats.total_requests.toLocaleString('ru-RU')}
                sub={`~${stats.avg_per_day} в день`}
                color="var(--accent)"
              />
              <MetricCard
                icon={Clock}
                label="Сэкономлено часов"
                value={`${stats.total_hours.toLocaleString('ru-RU')} ч`}
                sub={`${stats.total_minutes.toLocaleString('ru-RU')} мин`}
                color="var(--success)"
              />
              <MetricCard
                icon={Users}
                label="Уникальных пользователей"
                value={stats.unique_users.toLocaleString('ru-RU')}
                sub="анонимно"
                color="var(--cyan)"
              />
            </div>

            {/* Mode + Sub-category charts */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, marginBottom: 16 }}>
              <ChartCard title="Запросы по режимам">
                {stats.by_mode.length === 0 ? (
                  <div style={{ color: 'var(--text-dim)', fontSize: 13, padding: '20px 0', textAlign: 'center' }}>Нет данных</div>
                ) : (
                  <ResponsiveContainer width="100%" height={220}>
                    <BarChart data={stats.by_mode} margin={{ top: 4, right: 8, left: -16, bottom: 40 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                      <XAxis dataKey="label" tick={{ fontSize: 11, fill: 'var(--text-dim)' }} angle={-20} textAnchor="end" interval={0} />
                      <YAxis tick={{ fontSize: 11, fill: 'var(--text-dim)' }} allowDecimals={false} />
                      <Tooltip content={<CustomTooltipBar />} />
                      <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                        {stats.by_mode.map((entry) => (
                          <Cell key={entry.mode} fill={MODE_COLORS[entry.mode] || '#1e6fc0'} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </ChartCard>

              <ChartCard title='Подкатегории режима «Документы»'>
                {stats.by_sub_category.length === 0 ? (
                  <div style={{ color: 'var(--text-dim)', fontSize: 13, padding: '20px 0', textAlign: 'center' }}>Нет данных по подкатегориям</div>
                ) : (
                  <ResponsiveContainer width="100%" height={220}>
                    <BarChart data={stats.by_sub_category} margin={{ top: 4, right: 8, left: -16, bottom: 60 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                      <XAxis dataKey="label" tick={{ fontSize: 11, fill: 'var(--text-dim)' }} angle={-25} textAnchor="end" interval={0} />
                      <YAxis tick={{ fontSize: 11, fill: 'var(--text-dim)' }} allowDecimals={false} />
                      <Tooltip content={<CustomTooltipBar />} />
                      <Bar dataKey="count" radius={[4, 4, 0, 0]}>
                        {stats.by_sub_category.map((entry) => (
                          <Cell key={entry.sub_category} fill={SUB_COLORS[entry.sub_category] || '#1e6fc0'} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </ChartCard>
            </div>

            {/* Trend chart */}
            <ChartCard title="Динамика запросов" style={{ marginBottom: 16 }}>
              {stats.trend.length === 0 ? (
                <div style={{ color: 'var(--text-dim)', fontSize: 13, padding: '20px 0', textAlign: 'center' }}>Нет данных</div>
              ) : (
                <ResponsiveContainer width="100%" height={200}>
                  <LineChart data={stats.trend} margin={{ top: 4, right: 16, left: -16, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                    <XAxis
                      dataKey="date"
                      tick={{ fontSize: 10, fill: 'var(--text-dim)' }}
                      interval="preserveStartEnd"
                      tickFormatter={d => d.length === 7 ? d : d.slice(5)}
                    />
                    <YAxis tick={{ fontSize: 11, fill: 'var(--text-dim)' }} allowDecimals={false} />
                    <Tooltip
                      formatter={(v) => [v, 'Запросов']}
                      labelFormatter={(l) => `Дата: ${l}`}
                      contentStyle={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 6, fontSize: 13 }}
                    />
                    <Line
                      type="monotone"
                      dataKey="count"
                      stroke="var(--accent)"
                      strokeWidth={2}
                      dot={stats.trend.length < 40}
                      activeDot={{ r: 5 }}
                    />
                  </LineChart>
                </ResponsiveContainer>
              )}
            </ChartCard>

            {/* Bottom row: by hour + top skills */}
            <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 16 }}>
              <ChartCard title="Активность по часам суток">
                <ResponsiveContainer width="100%" height={180}>
                  <BarChart data={stats.by_hour} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                    <XAxis dataKey="hour" tick={{ fontSize: 10, fill: 'var(--text-dim)' }} interval={1} />
                    <YAxis tick={{ fontSize: 11, fill: 'var(--text-dim)' }} allowDecimals={false} />
                    <Tooltip
                      formatter={(v) => [v, 'Запросов']}
                      labelFormatter={(l) => `${l}:00`}
                      contentStyle={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 6, fontSize: 13 }}
                    />
                    <Bar dataKey="count" fill="var(--cyan)" radius={[3, 3, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </ChartCard>

              <ChartCard title="Топ активных скиллов">
                {stats.top_skills.length === 0 ? (
                  <div style={{ color: 'var(--text-dim)', fontSize: 13, padding: '20px 0', textAlign: 'center' }}>Нет данных</div>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {stats.top_skills.slice(0, 8).map((s, i) => {
                      const max = stats.top_skills[0].count;
                      const pct = Math.round((s.count / max) * 100);
                      return (
                        <div key={s.name}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 3 }}>
                            <span style={{ color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '75%' }}>{s.name}</span>
                            <span style={{ color: 'var(--text-dim)', fontVariantNumeric: 'tabular-nums', flexShrink: 0 }}>{s.count}</span>
                          </div>
                          <div style={{ height: 4, background: 'var(--bg-elevated)', borderRadius: 2 }}>
                            <div style={{ height: 4, width: `${pct}%`, background: CHART_COLORS[i % CHART_COLORS.length], borderRadius: 2, transition: 'width .4s' }} />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </ChartCard>
            </div>

            {/* Savings breakdown table */}
            {stats.by_mode.length > 0 && (
              <ChartCard title="Экономия времени юристов по категориям" style={{ marginTop: 16 }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid var(--border)' }}>
                      <th style={{ textAlign: 'left', padding: '8px 12px', color: 'var(--text-dim)', fontWeight: 600 }}>Категория</th>
                      <th style={{ textAlign: 'right', padding: '8px 12px', color: 'var(--text-dim)', fontWeight: 600 }}>Запросов</th>
                      <th style={{ textAlign: 'right', padding: '8px 12px', color: 'var(--text-dim)', fontWeight: 600 }}>Сэкономлено</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[
                      ...stats.by_mode.filter(m => m.mode !== 'document_gen').map(m => ({
                        label: m.label,
                        count: m.count,
                        minutes: m.minutes,
                        color: MODE_COLORS[m.mode],
                      })),
                      ...stats.by_sub_category.map(s => ({
                        label: `↳ ${s.label}`,
                        count: s.count,
                        minutes: s.minutes,
                        color: SUB_COLORS[s.sub_category],
                      })),
                    ].map((row, i) => (
                      <tr key={i} style={{ borderBottom: '1px solid var(--border)', transition: 'background .12s' }}
                        onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-hover)'}
                        onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
                        <td style={{ padding: '9px 12px', display: 'flex', alignItems: 'center', gap: 8 }}>
                          <span style={{ width: 10, height: 10, borderRadius: '50%', background: row.color, display: 'inline-block', flexShrink: 0 }} />
                          {row.label}
                        </td>
                        <td style={{ padding: '9px 12px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{row.count}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right', fontWeight: 600, color: 'var(--success)' }}>
                          {row.minutes >= 60
                            ? `${Math.floor(row.minutes / 60)} ч ${row.minutes % 60 > 0 ? `${row.minutes % 60} мин` : ''}`
                            : `${row.minutes} мин`}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </ChartCard>
            )}
          </>
        )}
      </div>

      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}
