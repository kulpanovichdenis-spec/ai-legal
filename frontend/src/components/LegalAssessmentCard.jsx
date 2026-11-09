import React, { useState } from 'react';
import './LegalAssessmentCard.css';

const VERDICT_CONFIG = {
  ok:      { label: 'Рисков не выявлено', icon: '✓', cls: 'lac-ok' },
  warning: { label: 'Требует внимания',   icon: '⚠', cls: 'lac-warning' },
  danger:  { label: 'Высокий риск',       icon: '✕', cls: 'lac-danger' },
};

const RISK_LEVEL_LABELS = {
  low:    'Низкий',
  medium: 'Средний',
  high:   'Высокий',
};

export default function LegalAssessmentCard({ data, degraded }) {
  const [expanded, setExpanded] = useState(true);

  if (!data) return null;

  const vc = VERDICT_CONFIG[data.verdict] || VERDICT_CONFIG.ok;
  const riskLabel = RISK_LEVEL_LABELS[data.riskLevel] || '';
  const hasContent = (data.risks?.length > 0) || (data.recommendations?.length > 0);

  return (
    <div className={`lac-card ${vc.cls}`}>

      {/* Header strip */}
      <button
        className="lac-header"
        onClick={() => setExpanded(e => !e)}
        aria-expanded={expanded}
      >
        <span className="lac-icon">{vc.icon}</span>
        <span className="lac-title">Юридическая оценка</span>
        <span className="lac-verdict-label">{vc.label}</span>
        {riskLabel && (
          <span className="lac-risk-badge">
            Риск: {riskLabel}
          </span>
        )}
        {degraded && (
          <span className="lac-degraded-note">авто</span>
        )}
        <span className="lac-chevron">{expanded ? '▲' : '▼'}</span>
      </button>

      {/* Expandable body */}
      {expanded && hasContent && (
        <div className="lac-body">
          {data.risks && data.risks.length > 0 && (
            <div className="lac-section">
              <div className="lac-section-title">Выявленные риски</div>
              <ul className="lac-list lac-list-risks">
                {data.risks.map((r, i) => <li key={i}>{r}</li>)}
              </ul>
            </div>
          )}
          {data.recommendations && data.recommendations.length > 0 && (
            <div className="lac-section">
              <div className="lac-section-title">Рекомендации</div>
              <ul className="lac-list lac-list-recs">
                {data.recommendations.map((r, i) => <li key={i}>{r}</li>)}
              </ul>
            </div>
          )}
          {data.disclaimer && (
            <div className="lac-disclaimer">{data.disclaimer}</div>
          )}
        </div>
      )}

      {/* Collapsed: show disclaimer inline */}
      {!expanded && data.disclaimer && (
        <div className="lac-disclaimer lac-disclaimer-collapsed">{data.disclaimer}</div>
      )}
    </div>
  );
}
