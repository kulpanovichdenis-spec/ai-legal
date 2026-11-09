import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Briefcase, Calendar, User, ArrowRight, Scale } from 'lucide-react';

function fmtDate(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleDateString('ru-RU', {
    day: '2-digit', month: 'long', year: 'numeric',
  });
}

export default function ProjectsListPage() {
  const [projects, setProjects] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    fetch('/api/public/projects')
      .then(r => r.ok ? r.json() : Promise.reject(`HTTP ${r.status}`))
      .then(data => { if (!cancelled) setProjects(data); })
      .catch(e => { if (!cancelled) setError(String(e)); });
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="projects-page">
      <header className="projects-header">
        <div className="projects-header-inner">
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <Scale size={28} color="var(--accent)" />
            <div>
              <h1 className="projects-title">Проекты юридической службы</h1>
              <p className="projects-subtitle">
                Что мы успели сделать пока AI юрист помогал в решении ваших вопросов
              </p>
            </div>
          </div>
        </div>
      </header>

      <main className="projects-main">
        {error && (
          <div className="project-notfound">
            <h2>Не удалось загрузить проекты</h2>
            <p style={{ color: 'var(--text-muted)' }}>{error}</p>
          </div>
        )}

        {!error && projects === null && (
          <div className="project-notfound">
            <p style={{ color: 'var(--text-muted)' }}>Загрузка…</p>
          </div>
        )}

        {!error && projects !== null && projects.length === 0 && (
          <div className="project-notfound">
            <h2>Пока нет опубликованных проектов</h2>
            <p style={{ color: 'var(--text-muted)' }}>
              Юристы делают разборы своих кейсов в режиме «Презентация для бизнеса»
              и публикуют сюда. Зайдите чуть позже.
            </p>
          </div>
        )}

        {!error && projects !== null && projects.length > 0 && (
          <div className="projects-grid">
            {projects.map(p => (
              <Link
                key={p.slug}
                to={`/projects/${p.slug}`}
                className="project-card"
              >
                <div className="project-card-icon">
                  <Briefcase size={20} />
                </div>
                <h3 className="project-card-title">{p.title}</h3>
                <p className="project-card-task">{p.slide_task}</p>
                <div className="project-card-footer">
                  {p.author_name && (
                    <span className="project-meta">
                      <User size={13} /> {p.author_name}
                    </span>
                  )}
                  <span className="project-meta">
                    <Calendar size={13} /> {fmtDate(p.created_at)}
                  </span>
                </div>
                <div className="project-card-cta">
                  Подробнее <ArrowRight size={14} />
                </div>
              </Link>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
