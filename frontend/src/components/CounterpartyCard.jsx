import React, { useState } from 'react';
import './CounterpartyCard.css';

const VERDICT_CONFIG = {
  ok:      { label: 'Действующий',      bgColor: 'var(--success-soft)', textColor: 'var(--success)', dotColor: '#1a8a53' },
  warning: { label: 'Требует внимания', bgColor: 'var(--warning-soft)', textColor: 'var(--warning)', dotColor: '#b96d00' },
  danger:  { label: 'Риск',             bgColor: 'var(--danger-soft)',  textColor: 'var(--danger)',  dotColor: '#c02030' },
};

function fmtYears(years) {
  if (years == null) return '';
  const intPart = Math.floor(years);
  const hasHalf = years % 1 >= 0.25 && years % 1 < 0.75;
  const display = hasHalf ? `${intPart}½` : `${intPart}`;
  const r = intPart % 10;
  const r100 = intPart % 100;
  let word;
  if (r100 >= 11 && r100 <= 19) word = 'лет';
  else if (r === 1) word = 'год';
  else if (r >= 2 && r <= 4) word = 'года';
  else word = 'лет';
  return `${display} ${word}`;
}

function fmtMoney(v) {
  if (v == null) return '';
  return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(v) + ' руб.';
}

function fmtShare(share) {
  if (share == null) return '';
  const pct = Math.round(share * 10000) / 100;
  return `${pct}%`;
}

function fmtDate(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleDateString('ru-RU');
  } catch {
    return iso;
  }
}

export default function CounterpartyCard({ card, degraded }) {
  const [okvedExpanded, setOkvedExpanded] = useState(false);

  if (!card) return null;

  const { header, summary, keyMetrics, details, founders, financials, okved, licenses, limitations, generatedAt } = card;
  const verdict = VERDICT_CONFIG[summary?.verdict] || VERDICT_CONFIG.ok;
  const genDate = generatedAt ? new Date(generatedAt).toLocaleDateString('ru-RU') : '';

  const extraOkveds = okved?.additional || [];
  const showExpandBtn = extraOkveds.length > 3;
  const visibleOkveds = okvedExpanded ? extraOkveds : extraOkveds.slice(0, 3);

  const hasDetails = (
    details?.registrationDate ||
    details?.ftsName ||
    details?.smb ||
    details?.phones?.length > 0 ||
    details?.emails?.length > 0
  );

  const hasMetrics = (
    keyMetrics?.ageYears != null ||
    keyMetrics?.charterCapital != null ||
    keyMetrics?.headcount != null ||
    details?.managementName
  );

  return (
    <div className="cc-card">

      {/* Header */}
      <div className="cc-header">
        {header.opfShort && <div className="cc-opf-badge">{header.opfShort}</div>}
        <div className="cc-name">{header.fullName}</div>
        <div className="cc-ids">
          {header.inn  && <span><b>ИНН</b>{header.inn}</span>}
          {header.ogrn && <span><b>ОГРН</b>{header.ogrn}</span>}
          {header.kpp  && <span><b>КПП</b>{header.kpp}</span>}
        </div>
      </div>

      {/* Verdict strip */}
      <div className="cc-verdict" style={{ background: verdict.bgColor, color: verdict.textColor }}>
        <span className="cc-verdict-dot" style={{ background: verdict.dotColor }} />
        <span>{verdict.label}</span>
        {keyMetrics?.ageYears != null && (
          <span className="cc-verdict-age"> · {fmtYears(keyMetrics.ageYears)}</span>
        )}
        {keyMetrics?.headcount != null && (
          <span className="cc-verdict-emp"> · {keyMetrics.headcount} сотр.</span>
        )}
      </div>

      {/* Invalidity alerts */}
      {limitations && limitations.length > 0 && limitations.map((lim, i) => (
        <div key={i} className={`cc-alert ${summary?.verdict === 'danger' ? 'cc-alert-danger' : 'cc-alert-warning'}`}>
          ⚠ {lim}
        </div>
      ))}

      {/* Key metrics */}
      {hasMetrics && (
        <div className="cc-metrics">
          {details?.managementName && (
            <div className="cc-metric-row">
              <div className="cc-metric-label">Руководитель</div>
              <div className="cc-metric-value">
                {details.managementName}
                {details.managementPost ? ` (${details.managementPost})` : ''}
              </div>
            </div>
          )}
          {(details?.addressFull || details?.address) && (
            <div className="cc-metric-row">
              <div className="cc-metric-label">Адрес</div>
              <div className="cc-metric-value cc-metric-address">
                {details.addressFull || details.address}
              </div>
            </div>
          )}
          {keyMetrics?.registrationDate && (
            <div className="cc-metric-row">
              <div className="cc-metric-label">Зарегистрирован</div>
              <div className="cc-metric-value">
                {fmtDate(keyMetrics.registrationDate)}
                {keyMetrics.ageYears != null && ` — ${fmtYears(keyMetrics.ageYears)}`}
              </div>
            </div>
          )}
          {keyMetrics?.charterCapital != null && (
            <div className="cc-metric-row">
              <div className="cc-metric-label">Уставный капитал</div>
              <div className="cc-metric-value">{fmtMoney(keyMetrics.charterCapital)}</div>
            </div>
          )}
        </div>
      )}

      {/* Details: tax authority, SMB, contacts */}
      {hasDetails && (
        <div className="cc-details">
          {details.ftsName && (
            <div className="cc-detail-row">
              <span className="cc-detail-label">Налоговый орган</span>
              <span>{details.ftsName}</span>
            </div>
          )}
          {details.smb && (
            <div className="cc-detail-row">
              <span className="cc-detail-label">Реестр МСП</span>
              <span>{details.smb}{details.smbDate ? ` (с ${details.smbDate})` : ''}</span>
            </div>
          )}
          {(details.phones?.length > 0 || details.emails?.length > 0) && (
            <div className="cc-detail-row">
              <span className="cc-detail-label">Контакты</span>
              <span>{[...(details.phones || []), ...(details.emails || [])].join(' · ')}</span>
            </div>
          )}
        </div>
      )}

      {/* OKVED */}
      {okved?.primary && (
        <div className="cc-section">
          <div className="cc-section-title">ОКВЭД</div>
          <div className="cc-okved-main">
            <span className="cc-okved-code">{okved.primary.code}</span>
            {okved.primary.name && <span className="cc-okved-name"> — {okved.primary.name}</span>}
          </div>
          {extraOkveds.length > 0 && (
            <div className="cc-okved-extra">
              {visibleOkveds.map((o, i) => (
                <span key={i} className="cc-okved-chip" title={o.name}>{o.code}</span>
              ))}
              {showExpandBtn && (
                <button className="cc-expand-btn" onClick={() => setOkvedExpanded(e => !e)}>
                  {okvedExpanded ? 'Скрыть' : `+${extraOkveds.length - 3} ещё`}
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {/* Founders */}
      {founders && founders.length > 0 && (
        <div className="cc-section">
          <div className="cc-section-title">Учредители</div>
          {founders.map((f, i) => (
            <div key={i} className="cc-founder-row">
              <span className="cc-founder-name" style={f.invalid ? { color: 'var(--danger)' } : {}}>
                {f.name}
              </span>
              {f.share != null && (
                <span className="cc-founder-share">{fmtShare(f.share)}</span>
              )}
              {f.invalid && (
                <span className="cc-invalid-badge" title={f.invalidityLabel || ''}>
                  недостоверно
                </span>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Financials */}
      {financials?.year && (
        <div className="cc-section">
          <div className="cc-section-title">Финансы ({financials.year} г.)</div>
          <div className="cc-fin-grid">
            {financials.revenue != null && (
              <div className="cc-fin-item">
                <div className="cc-fin-label">Выручка</div>
                <div className="cc-fin-value">{fmtMoney(financials.revenue)}</div>
              </div>
            )}
            {financials.income != null && (
              <div className="cc-fin-item">
                <div className="cc-fin-label">Доходы</div>
                <div className="cc-fin-value">{fmtMoney(financials.income)}</div>
              </div>
            )}
            {financials.expense != null && (
              <div className="cc-fin-item">
                <div className="cc-fin-label">Расходы</div>
                <div className="cc-fin-value">{fmtMoney(financials.expense)}</div>
              </div>
            )}
            {financials.debt != null && (
              <div className="cc-fin-item cc-fin-item-debt">
                <div className="cc-fin-label">Недоимки</div>
                <div className="cc-fin-value">{fmtMoney(financials.debt)}</div>
              </div>
            )}
          </div>
          {financials.taxSystem && (
            <div className="cc-fin-tax">Система налогообложения: {financials.taxSystem}</div>
          )}
        </div>
      )}

      {/* Licenses */}
      {licenses && licenses.length > 0 && (
        <div className="cc-section">
          <div className="cc-section-title">Лицензии ({licenses.length})</div>
          {licenses.slice(0, 3).map((l, i) => (
            <div key={i} className="cc-license">{l}</div>
          ))}
        </div>
      )}

      <div className="cc-footer">
        Данные: DaData{genDate ? ` · ${genDate}` : ''}
        {degraded && <span className="cc-footer-degraded"> · Юридическая оценка недоступна</span>}
      </div>
    </div>
  );
}
