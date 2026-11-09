import React from 'react';
import CounterpartyCard from './CounterpartyCard.jsx';
import LegalAssessmentCard from './LegalAssessmentCard.jsx';

const SUPPORTED_VERSION = 1;

export default function CheckResponse({ data }) {
  if (!data) return null;

  if (data.schemaVersion !== SUPPORTED_VERSION) {
    return (
      <div style={{
        padding: '8px 12px', margin: '4px 0 10px',
        background: 'var(--warning-soft)', color: 'var(--warning)',
        borderRadius: 'var(--radius)', fontSize: 13,
      }}>
        Неизвестная версия карточки (v{data.schemaVersion}). Обновите страницу.
      </div>
    );
  }

  const counterpartyBlock = data.blocks?.find(b => b.type === 'counterparty');
  const assessmentBlock   = data.blocks?.find(b => b.type === 'legalAssessment');

  return (
    <div style={{ margin: '4px 0 2px' }}>
      {counterpartyBlock && (
        <CounterpartyCard
          card={counterpartyBlock.data}
          degraded={data.meta?.degraded}
        />
      )}
      {assessmentBlock && (
        <LegalAssessmentCard
          data={assessmentBlock.data}
          degraded={data.meta?.degraded}
        />
      )}
    </div>
  );
}
