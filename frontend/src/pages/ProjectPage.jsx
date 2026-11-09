import React, { useState, useEffect } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ArrowLeft, User, Calendar, Target, Zap, CheckSquare, TrendingUp, ArrowRight, Scale } from 'lucide-react';

function fmtDate(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleDateString('ru-RU', {
    day: '2-digit', month: 'long', year: 'numeric',
  });
}

const SLIDES = [
  { key: 'slide_task',       num: 1, label: 'Задача',     icon: Target,     accent: '#2c5aa0' },
  { key: 'slide_complexity', num: 2, label: 'Сложность',  icon: Zap,        accent: '#b8650e' },
  { key: 'slide_action',     num: 3, label: 'Что сделано', icon: CheckSquare, accent: '#1f7a4e' },
  { key: 'slide_effect',     num: 4, label: 'Эффект',     icon: TrendingUp, accent: '#a82828' },
  { key: 'slide_next',       num: 5, label: 'Дальше',     icon: ArrowRight, accent: '#5b3aa0' },
];

// Распознаём, является ли блок маркированным списком (буллеты с «- ») —
// тогда рендерим как <ul>. Иначе — абзацы.
function renderSlideContent(text) {
  const trimmed = (text || '').trim();
  if (!trimmed) return null;
  const lines = trimmed.split(/\r?\n/);
  const isList = lines.every(l => /^\s*[-*•]\s+/.test(l) || !l.trim());
  if (isList) {
    const items = lines.filter(l => /^\s*[-*•]\s+/.test(l))
      .map(l => l.replace(/^\s*[-*•]\s+/, '').trim());
    return (
      <ul className="slide-bullets">
        {items.map((it, i) => <li key={i}>{it}</li>)}
      </ul>
    );
  }
  return <p>{trimmed}</p>;
}

export default function ProjectPage() {
  const { slug } = useParams();
  const [project, setProject] = useState(null);
  const [status, setStatus] = useState('loading'); // loading|ok|notfound|error
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`/api/public/projects/${encodeURIComponent(slug)}`)
      .then(async r => {
        if (r.status === 404) { setStatus('notfound'); return null; }
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(data => {
        if (cancelled) return;
        if (data) { setProject(data); setStatus('ok'); }
      })
      .catch(e => {
        if (cancelled) return;
        setError(String(e)); setStatus('error');
      });
    return () => { cancelled = true; };
  }, [slug]);

  if (status === 'loading') {
    return (
      <div className="projects-page">
        <div className="projects-main">
          <div className="project-notfound">
            <p style={{ color: 'var(--text-muted)' }}>Загрузка…</p>
          </div>
        </div>
      </div>
    );
  }
  if (status === 'notfound' || (status === 'ok' && !project)) {
    return (
      <div className="projects-page">
        <div className="projects-main">
          <div className="project-notfound">
            <h2>Проект не найден</h2>
            <Link to="/projects" className="btn btn-primary">К списку проектов</Link>
          </div>
        </div>
      </div>
    );
  }
  if (status === 'error') {
    return (
      <div className="projects-page">
        <div className="projects-main">
          <div className="project-notfound">
            <h2>Ошибка загрузки</h2>
            <p style={{ color: 'var(--text-muted)' }}>{error}</p>
            <Link to="/projects" className="btn btn-primary" style={{ marginTop: 16 }}>
              К списку
            </Link>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="projects-page">
      <header className="projects-header">
        <div className="projects-header-inner">
          <Link to="/projects" className="back-link">
            <ArrowLeft size={16} /> К списку проектов
          </Link>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 18 }}>
            <Scale size={24} color="var(--accent)" />
            <h1 className="projects-title" style={{ fontSize: 24 }}>{project.title}</h1>
          </div>
          <div className="project-meta-row">
            {project.author_name && (
              <span className="project-meta">
                <User size={13} /> {project.author_name}
              </span>
            )}
            <span className="project-meta">
              <Calendar size={13} /> {fmtDate(project.created_at)}
            </span>
          </div>
        </div>
      </header>

      <main className="projects-main">
        <div className="slides-stack">
          {SLIDES.map(s => {
            const content = (project[s.key] || '').trim();
            if (!content) return null;
            const Icon = s.icon;
            return (
              <article key={s.key} className="slide-card">
                <div className="slide-card-header" style={{ background: s.accent }}>
                  <span className="slide-num">Слайд {s.num}</span>
                  <span className="slide-label">
                    <Icon size={16} /> {s.label}
                  </span>
                </div>
                <div className="slide-card-body">
                  {renderSlideContent(content)}
                </div>
              </article>
            );
          })}
        </div>
      </main>
    </div>
  );
}
