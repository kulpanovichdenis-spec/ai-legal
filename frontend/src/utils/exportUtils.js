import {
  Document, Packer, Paragraph, TextRun,
  HeadingLevel, BorderStyle, AlignmentType,
  Table, TableRow, TableCell, WidthType, TableLayoutType,
  UnderlineType,
} from 'docx';

// ── Константы внутреннего стандарта оформления ────────────────────────────────
const FONT        = 'Arial';
const SIZE        = 22;          // 11pt = 22 half-points
const SIZE_SMALL  = 20;          // 10pt
const LINE_150    = { line: 276, lineRule: 'auto' };   // межстрочный 1,15
const INDENT_PARA = { firstLine: 0 };                  // без абзацного отступа
// Поля по образцу деловой претензии: верх/низ/право 2,0 см, лево 2,5 см (под подшивку)
const MARGINS     = { top: 1134, bottom: 1134, left: 1417, right: 1134 };
const SUBITEM_INDENT = 567;      // 1 см — отступ для подпунктов (a), (b), (c)
// Размер страницы A4 в twips (210×297 мм). Задаём явно: иначе ширина зависит
// от дефолта библиотеки (US Letter), и таблицы рассчитываются под чужой формат.
const PAGE_A4     = { width: 11906, height: 16838 };
// Полезная ширина под содержимое = ширина A4 − левое и правое поля.
const CONTENT_WIDTH_CHAT = PAGE_A4.width - 1440 - 1440;                  // экспорт чата (поля 2,54 см)
// Ширина таблиц в экспорте документа — по эталонному файлу пользователя
// (Документ_..._исправил.docx): почти во всю ширину текста, минимальные поля
// ячеек, русская колонка шире английской (5232 / 4402 для двуязычной таблицы).
const TABLE_WIDTH_DOC   = 9634;
const TABLE_CELL_MARGIN = 10;   // поля ячейки слева/справа, twips
const TABLE_LEFT_COL    = 5232; // русская колонка двуязычной таблицы

// ── Форматирование данных карточки ────────────────────────────────────────────
function fmtMoney(v) {
  if (v == null) return '';
  return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(v) + ' руб.';
}
function fmtShare(s) {
  if (s == null) return '';
  return `${Math.round(s * 10000) / 100}%`;
}
function fmtDate(iso) {
  if (!iso) return '';
  try { return new Date(iso).toLocaleDateString('ru-RU'); } catch { return iso; }
}

// Строки карточки CounterpartyData → массив Paragraph для DOCX
function buildCounterpartyDocxRows(card) {
  if (!card) return [];
  const b = { font: FONT, size: SIZE_SMALL };
  const bold = { ...b, bold: true };
  const rows = [];

  const sectionHead = (text) => rows.push(new Paragraph({
    children: [new TextRun({ text, ...bold, color: '1e6fc0' })],
    spacing: { before: 120, after: 40 },
  }));
  const row = (label, value) => {
    if (!value) return;
    rows.push(new Paragraph({
      children: [
        new TextRun({ text: `${label}: `, ...bold }),
        new TextRun({ text: String(value), ...b }),
      ],
      spacing: { after: 40 },
    }));
  };
  const listItem = (text) => rows.push(new Paragraph({
    children: [new TextRun({ text: `• ${text}`, ...b })],
    indent: { left: 360 },
    spacing: { after: 30 },
  }));

  const { header, summary, keyMetrics, details, founders, financials, okved, licenses, limitations } = card;

  sectionHead('Контрагент');
  row('Наименование', header?.fullName);
  row('ИНН', header?.inn);
  row('ОГРН', header?.ogrn);
  if (header?.kpp) row('КПП', header.kpp);
  row('Статус', summary?.text || header?.status);

  if (details?.managementName) row('Руководитель', `${details.managementName}${details.managementPost ? ` (${details.managementPost})` : ''}`);
  if (details?.addressFull || details?.address) row('Адрес', details.addressFull || details.address);
  if (keyMetrics?.registrationDate) row('Зарегистрирован', `${fmtDate(keyMetrics.registrationDate)}`);
  if (keyMetrics?.charterCapital != null) row('Уставный капитал', fmtMoney(keyMetrics.charterCapital));
  if (keyMetrics?.headcount != null) row('Сотрудников', String(keyMetrics.headcount));

  if (okved?.primary) {
    row('ОКВЭД (осн.)', `${okved.primary.code}${okved.primary.name ? ' — ' + okved.primary.name : ''}`);
  }

  if (limitations?.length > 0) {
    sectionHead('Ограничения');
    limitations.forEach(l => listItem(l));
  }

  if (founders?.length > 0) {
    sectionHead('Учредители');
    founders.forEach(f => {
      const share = f.share != null ? ` — ${fmtShare(f.share)}` : '';
      const inv = f.invalid ? ' [недостоверно]' : '';
      listItem(`${f.name}${share}${inv}`);
    });
  }

  if (financials?.year) {
    sectionHead(`Финансы (${financials.year} г.)`);
    if (financials.revenue != null) row('Выручка', fmtMoney(financials.revenue));
    if (financials.income != null) row('Доходы', fmtMoney(financials.income));
    if (financials.expense != null) row('Расходы', fmtMoney(financials.expense));
    if (financials.debt != null) row('Недоимки', fmtMoney(financials.debt));
    if (financials.taxSystem) row('Система налогообложения', financials.taxSystem);
  }

  if (licenses?.length > 0) {
    sectionHead(`Лицензии (${licenses.length})`);
    licenses.slice(0, 5).forEach(l => listItem(l));
  }

  return rows;
}

// Строки юридической оценки LegalAssessmentData → массив Paragraph для DOCX
function buildAssessmentDocxRows(data) {
  if (!data) return [];
  const b = { font: FONT, size: SIZE_SMALL };
  const bold = { ...b, bold: true };
  const rows = [];

  const VERDICT_LABELS = { ok: 'Рисков не выявлено', warning: 'Требует внимания', danger: 'Высокий риск' };
  const RISK_LABELS    = { low: 'Низкий', medium: 'Средний', high: 'Высокий' };

  rows.push(new Paragraph({
    children: [new TextRun({ text: 'Юридическая оценка', ...bold, color: '1e6fc0' })],
    spacing: { before: 120, after: 40 },
  }));
  rows.push(new Paragraph({
    children: [
      new TextRun({ text: 'Вердикт: ', ...bold }),
      new TextRun({ text: VERDICT_LABELS[data.verdict] || data.verdict, ...b }),
      new TextRun({ text: '   Уровень риска: ', ...bold }),
      new TextRun({ text: RISK_LABELS[data.riskLevel] || data.riskLevel, ...b }),
    ],
    spacing: { after: 60 },
  }));

  if (data.risks?.length > 0) {
    rows.push(new Paragraph({ children: [new TextRun({ text: 'Риски:', ...bold })], spacing: { after: 30 } }));
    data.risks.forEach(r => rows.push(new Paragraph({
      children: [new TextRun({ text: `• ${r}`, ...b })],
      indent: { left: 360 }, spacing: { after: 30 },
    })));
  }
  if (data.recommendations?.length > 0) {
    rows.push(new Paragraph({ children: [new TextRun({ text: 'Рекомендации:', ...bold })], spacing: { before: 60, after: 30 } }));
    data.recommendations.forEach(r => rows.push(new Paragraph({
      children: [new TextRun({ text: `• ${r}`, ...b })],
      indent: { left: 360 }, spacing: { after: 30 },
    })));
  }
  if (data.disclaimer) {
    rows.push(new Paragraph({
      children: [new TextRun({ text: data.disclaimer, ...b, color: '888888', italics: true })],
      spacing: { before: 80, after: 40 },
    }));
  }
  return rows;
}

// HTML-блок карточки контрагента для PDF
function buildCounterpartyHtml(card) {
  if (!card) return '';
  const { header, summary, keyMetrics, details, founders, financials, okved, limitations, licenses } = card;
  const rows = [];
  const row = (label, value) => {
    if (!value) return;
    rows.push(`<tr><td class="cl">${label}</td><td>${value}</td></tr>`);
  };
  const esc = s => String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  row('Наименование', esc(header?.fullName));
  row('ИНН', esc(header?.inn));
  row('ОГРН', esc(header?.ogrn));
  if (header?.kpp) row('КПП', esc(header.kpp));
  row('Статус', esc(summary?.text || header?.status));
  if (details?.managementName) row('Руководитель', esc(`${details.managementName}${details.managementPost ? ' (' + details.managementPost + ')' : ''}`));
  if (details?.addressFull || details?.address) row('Адрес', esc(details.addressFull || details.address));
  if (keyMetrics?.registrationDate) row('Зарегистрирован', esc(fmtDate(keyMetrics.registrationDate)));
  if (keyMetrics?.charterCapital != null) row('Уставный капитал', esc(fmtMoney(keyMetrics.charterCapital)));
  if (okved?.primary) row('ОКВЭД', esc(`${okved.primary.code}${okved.primary.name ? ' — ' + okved.primary.name : ''}`));

  let extra = '';
  if (limitations?.length > 0)
    extra += `<p class="lim"><strong>Ограничения:</strong> ${limitations.map(l => esc(l)).join('; ')}</p>`;
  if (founders?.length > 0) {
    extra += '<p><strong>Учредители:</strong></p><ul>';
    founders.forEach(f => {
      const share = f.share != null ? ` — ${fmtShare(f.share)}` : '';
      extra += `<li>${esc(f.name)}${share}${f.invalid ? ' <em>[недостоверно]</em>' : ''}</li>`;
    });
    extra += '</ul>';
  }
  if (financials?.year) {
    extra += `<p><strong>Финансы (${esc(financials.year)} г.):</strong></p><ul>`;
    if (financials.revenue != null) extra += `<li>Выручка: ${fmtMoney(financials.revenue)}</li>`;
    if (financials.income != null)  extra += `<li>Доходы: ${fmtMoney(financials.income)}</li>`;
    if (financials.expense != null) extra += `<li>Расходы: ${fmtMoney(financials.expense)}</li>`;
    if (financials.debt != null)    extra += `<li>Недоимки: ${fmtMoney(financials.debt)}</li>`;
    extra += '</ul>';
  }
  if (licenses?.length > 0) {
    extra += `<p><strong>Лицензии (${licenses.length}):</strong></p><ul>`;
    licenses.slice(0, 5).forEach(l => extra += `<li>${esc(l)}</li>`);
    extra += '</ul>';
  }

  return `<div class="ccard"><h3>Контрагент</h3><table class="ctbl">${rows.join('')}</table>${extra}</div>`;
}

// HTML-блок юридической оценки для PDF
function buildAssessmentHtml(data) {
  if (!data) return '';
  const VERDICT_LABELS = { ok: 'Рисков не выявлено', warning: 'Требует внимания', danger: 'Высокий риск' };
  const RISK_LABELS    = { low: 'Низкий', medium: 'Средний', high: 'Высокий' };
  const esc = s => String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  let html = `<div class="ccard"><h3>Юридическая оценка</h3>`;
  html += `<p><strong>Вердикт:</strong> ${esc(VERDICT_LABELS[data.verdict] || data.verdict)} &nbsp; <strong>Риск:</strong> ${esc(RISK_LABELS[data.riskLevel] || data.riskLevel)}</p>`;
  if (data.risks?.length > 0) {
    html += '<p><strong>Риски:</strong></p><ul>' + data.risks.map(r => `<li>${esc(r)}</li>`).join('') + '</ul>';
  }
  if (data.recommendations?.length > 0) {
    html += '<p><strong>Рекомендации:</strong></p><ul>' + data.recommendations.map(r => `<li>${esc(r)}</li>`).join('') + '</ul>';
  }
  if (data.disclaimer) html += `<p class="disc">${esc(data.disclaimer)}</p>`;
  html += '</div>';
  return html;
}

// ── Утилиты ───────────────────────────────────────────────────────────────────
function todayRu() {
  return new Date().toLocaleDateString('ru-RU', {
    year: 'numeric', month: 'long', day: 'numeric',
  });
}
function dateSuffix() {
  return new Date().toISOString().slice(0, 10);
}
// Дата + время в имени файла: каждое скачивание уникально, чтобы новые версии
// документа не путались со старыми загрузками с тем же именем.
function stampSuffix() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}_`
       + `${p(d.getHours())}-${p(d.getMinutes())}-${p(d.getSeconds())}`;
}
function safeFilename(str) {
  return (str || 'export')
    .replace(/[<>:"/\\|?*\x00-\x1f]/g, '')
    .trim().replace(/\s+/g, '_').slice(0, 60) || 'export';
}
function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = filename;
  document.body.appendChild(a); a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

// ── Inline markdown → TextRun[] ───────────────────────────────────────────────
function parseInline(text, base = {}) {
  if (!text) return [new TextRun({ text: '', ...base })];
  const runs = [];
  const re = /(\*\*[^*\n]+?\*\*|\*[^*\n]+?\*|`[^`\n]+?`)/g;
  let last = 0, m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) {
      const t = text.slice(last, m.index);
      if (t) runs.push(new TextRun({ text: t, ...base }));
    }
    const raw = m[1];
    if (raw.startsWith('**'))
      runs.push(new TextRun({ text: raw.slice(2, -2), bold: true, ...base }));
    else if (raw.startsWith('*'))
      runs.push(new TextRun({ text: raw.slice(1, -1), italics: true, ...base }));
    else
      runs.push(new TextRun({ text: raw.slice(1, -1), font: 'Courier New', size: SIZE_SMALL }));
    last = m.index + raw.length;
  }
  if (last < text.length) {
    const t = text.slice(last);
    if (t) runs.push(new TextRun({ text: t, ...base }));
  }
  return runs.length ? runs : [new TextRun({ text, ...base })];
}

// ── Определение начала блока подписей ─────────────────────────────────────────
// СТРОГИЙ паттерн: строка целиком (не часть абзаца) — название стороны в верхнем
// регистре, опционально с двоеточием/слэшем. Не путать с прозой вроде
// «Покупатель исполнил обязанность…», где слово используется в обычном тексте.
const SIG_STRICT_PATTERN = /^(\*{0,2})(ИСПОЛНИТЕЛЬ|ЗАКАЗЧИК|ПОСТАВЩИК|ПОКУПАТЕЛЬ|АРЕНДАТОР|АРЕНДОДАТЕЛЬ|ЛИЦЕНЗИАР|ЛИЦЕНЗИАТ|СТОРОНА\s*1|СТОРОНА\s*2|ПРОДАВЕЦ|ПРИНЦИПАЛ|АГЕНТ|ПОДРЯДЧИК|ЗАЕМЩИК|ЗАЙМОДАВЕЦ)(\*{0,2})[\s:/-]*$/;

// Строка целиком из подчёркиваний (поле для подписи), а не плейсхолдер вроде [_____].
const FULL_UNDERLINE = /^_{4,}\s*$/;

function splitSignature(lines) {
  // Раскол на body/sig нужен ТОЛЬКО для двусторонних документов (договор, ДС),
  // где подпись Сторона 1 / Сторона 2 рисуется таблицей в две колонки.
  // Для односторонних писем (претензия, уведомление) подпись одна — она остаётся
  // в body и рендерится как обычный текст.
  //
  // Триггер двухколоночной подписи: ДВА разных party-маркера в последних 30 строках.
  const tailStart = Math.max(0, lines.length - 30);
  const partySeen = new Set();
  let firstPartyIdx = -1;
  for (let i = tailStart; i < lines.length; i++) {
    const l = lines[i].trim();
    const m = l.match(SIG_STRICT_PATTERN);
    if (m) {
      partySeen.add(m[2].toUpperCase().replace(/\s+/g, ''));
      if (firstPartyIdx === -1) firstPartyIdx = i;
    }
  }
  if (partySeen.size >= 2 && firstPartyIdx >= 0) {
    return { body: lines.slice(0, firstPartyIdx), sig: lines.slice(firstPartyIdx) };
  }
  return { body: lines, sig: [] };
}

// ── Построение блока подписей (таблица 2 колонки) ─────────────────────────────
function buildSignatureTable(sigLines) {
  // Разбиваем строки подписей на два столбца
  const clean = sigLines.filter(l => l.trim() && !l.match(/^---+$/));

  // Ищем разделитель между левой и правой колонкой
  // Обычно это пустая строка или наличие двух групп с party-именами
  const midIdx = (() => {
    for (let i = 1; i < clean.length; i++) {
      if (SIG_STRICT_PATTERN.test(clean[i].trim())) return i;
    }
    return Math.ceil(clean.length / 2);
  })();

  const left  = clean.slice(0, midIdx);
  const right = clean.slice(midIdx);

  const cellStyle = (lines) => lines.map(raw => {
    const text = raw.replace(/\*\*/g, '').trim();
    const isPartyName = SIG_STRICT_PATTERN.test(text);
    const isUnderline = FULL_UNDERLINE.test(text);
    if (isPartyName) {
      return new Paragraph({
        children: [new TextRun({ text, font: FONT, size: SIZE_SMALL, bold: true })],
        spacing: { before: 200, after: 80 },
      });
    }
    if (isUnderline) {
      return new Paragraph({
        children: [new TextRun({
          text: '______________________________',
          font: FONT, size: SIZE_SMALL,
          underline: { type: UnderlineType.SINGLE },
        })],
        spacing: { before: 360, after: 40 },
      });
    }
    return new Paragraph({
      children: parseInline(text, { font: FONT, size: SIZE_SMALL }),
      spacing: { after: 60 },
    });
  });

  const noBorder = { style: BorderStyle.NONE, size: 0, color: 'FFFFFF' };
  const borders  = { top: noBorder, bottom: noBorder, left: noBorder, right: noBorder };

  return new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    borders: { top: noBorder, bottom: noBorder, left: noBorder, right: noBorder,
               insideH: noBorder, insideV: noBorder },
    rows: [new TableRow({
      children: [
        new TableCell({
          width: { size: 48, type: WidthType.PERCENTAGE },
          borders,
          children: cellStyle(left.length ? left : ['СТОРОНА 1', '________________']),
        }),
        new TableCell({
          width: { size: 4, type: WidthType.PERCENTAGE },
          borders,
          children: [new Paragraph({ text: '' })],
        }),
        new TableCell({
          width: { size: 48, type: WidthType.PERCENTAGE },
          borders,
          children: cellStyle(right.length ? right : ['СТОРОНА 2', '________________']),
        }),
      ],
    })],
  });
}

// Парсит markdown-строку таблицы "| col1 | col2 | col3 |" в массив ячеек
function parseTableRow(line) {
  const trimmed = line.trim();
  if (!trimmed.startsWith('|') || !trimmed.endsWith('|')) return null;
  return trimmed.slice(1, -1).split('|').map(c => c.trim());
}
function isTableSeparator(line) {
  const cells = parseTableRow(line);
  if (!cells) return false;
  return cells.every(c => /^:?-{3,}:?$/.test(c));
}

// Строит DOCX-таблицу из массива строк (заголовок + данные).
// tableWidth — целевая ширина таблицы. Для двуязычной (2 колонки) русская
// колонка делается шире английской — как в эталонном файле пользователя.
function buildContentTable(rowsData, tableWidth = TABLE_WIDTH_DOC) {
  const base = { font: FONT, size: SIZE };
  const cols = rowsData[0].length;
  let colWidths;
  if (cols === 2) {
    const left = Math.min(TABLE_LEFT_COL, tableWidth - 1000);
    colWidths = [left, tableWidth - left];
  } else {
    const w = Math.floor(tableWidth / cols);
    colWidths = Array(cols).fill(w);
  }
  const totalWidth = colWidths.reduce((a, b) => a + b, 0);
  const cellMargins = { left: TABLE_CELL_MARGIN, right: TABLE_CELL_MARGIN };
  const borders = {
    top:    { style: BorderStyle.SINGLE, size: 4, color: '000000' },
    bottom: { style: BorderStyle.SINGLE, size: 4, color: '000000' },
    left:   { style: BorderStyle.SINGLE, size: 4, color: '000000' },
    right:  { style: BorderStyle.SINGLE, size: 4, color: '000000' },
    insideHorizontal: { style: BorderStyle.SINGLE, size: 4, color: '000000' },
    insideVertical:   { style: BorderStyle.SINGLE, size: 4, color: '000000' },
  };
  const rows = rowsData.map((row, ri) => new TableRow({
    children: row.map((cellText, ci) => new TableCell({
      width: { size: colWidths[ci] ?? colWidths[colWidths.length - 1], type: WidthType.DXA },
      margins: cellMargins,
      children: (cellText || '').split(/<br\s*\/?>/gi).flatMap(part => {
        const p = part.trim();
        if (!p) return [];
        let hm;
        if ((hm = p.match(/^# (.+)/)))
          return [new Paragraph({
            alignment: AlignmentType.LEFT,
            spacing: { after: 40 },
            children: [new TextRun({ text: hm[1].toUpperCase(), ...base, bold: true })],
          })];
        if ((hm = p.match(/^## (.+)/)))
          return [new Paragraph({
            alignment: AlignmentType.LEFT,
            spacing: { before: 160, after: 40 },
            children: parseInline(hm[1], { ...base, bold: true }),
          })];
        return [new Paragraph({
          alignment: AlignmentType.LEFT,
          spacing: { after: 40 },
          children: parseInline(p, ri === 0 ? { ...base, bold: true } : base),
        })];
      }),
    })),
  }));
  return new Table({
    width: { size: totalWidth, type: WidthType.DXA },
    columnWidths: colWidths,
    layout: TableLayoutType.FIXED,
    borders,
    rows,
  });
}

// ── Основной парсер → (Paragraph|Table)[] ──────────────────────────────────────
function buildLegalParagraphs(lines, tableWidth = TABLE_WIDTH_DOC) {
  const result = [];
  const base   = { font: FONT, size: SIZE };

  for (let idx = 0; idx < lines.length; idx++) {
    const raw = lines[idx];
    const line = raw.trimEnd();
    let m;

    // Markdown-таблица: сгруппировать подряд идущие строки с | ... |
    const tableCells = parseTableRow(line);
    if (tableCells) {
      const rowsData = [tableCells];
      let j = idx + 1;
      // Пропустить строку-разделитель
      if (j < lines.length && isTableSeparator(lines[j])) j++;
      // Собрать строки данных
      while (j < lines.length) {
        const next = parseTableRow(lines[j]);
        if (!next) break;
        if (isTableSeparator(lines[j])) { j++; continue; }
        rowsData.push(next);
        j++;
      }
      if (rowsData.length >= 1) {
        result.push(buildContentTable(rowsData, tableWidth));
        // Небольшой пробел после таблицы
        result.push(new Paragraph({ text: '', spacing: { after: 80 } }));
        idx = j - 1;
        continue;
      }
    }

    // Заголовок документа  # ...
    if ((m = line.match(/^# (.+)/))) {
      result.push(new Paragraph({
        alignment: AlignmentType.CENTER,
        spacing: { before: 0, after: 120 },
        children: [new TextRun({
          text: m[1].toUpperCase(),
          font: FONT, size: SIZE, bold: true,
        })],
      }));

    // Раздел ## N. ...
    } else if ((m = line.match(/^## (.+)/))) {
      result.push(new Paragraph({
        spacing: { before: 280, after: 80 },
        indent: INDENT_PARA,
        children: parseInline(m[1], { ...base, bold: true }),
      }));

    // Подраздел ### ...
    } else if ((m = line.match(/^### (.+)/))) {
      result.push(new Paragraph({
        spacing: { before: 160, after: 60 },
        indent: INDENT_PARA,
        children: parseInline(m[1], { ...base, bold: true, italics: true }),
      }));

    // Горизонтальная черта
    } else if (line.match(/^---+$/)) {
      result.push(new Paragraph({
        text: '',
        border: { bottom: { style: BorderStyle.SINGLE, size: 1, color: 'AAAAAA', space: 1 } },
        spacing: { after: 120 },
      }));

    // Строка «г. Москва» / место и дата
    } else if (line.match(/^г\.\s*[А-Я]|^«[_\d]+»|^город\s/i)) {
      result.push(new Paragraph({
        alignment: AlignmentType.LEFT,
        spacing: { before: 120, after: 120 },
        children: parseInline(line, { ...base, size: SIZE_SMALL }),
      }));

    // Шапка по правому краю: строка, начинающаяся с `>> ` или `>>>`
    // (используется для исходящего номера, даты, адресата справа сверху)
    } else if ((m = line.match(/^>>+\s*(.*)/))) {
      result.push(new Paragraph({
        alignment: AlignmentType.RIGHT,
        spacing: { after: 60 },
        children: parseInline(m[1], base),
      }));

    // Римский заголовок раздела «I. Факты», «II. Договорные основания», ...
    // Только если строка — заголовок: короткая, без запятых, без середины-предложения.
    } else if ((m = line.match(/^([IVX]{1,4})\.\s+(.{1,60})$/)) && !m[2].includes(',') && !/[.!?]\s/.test(m[2])) {
      result.push(new Paragraph({
        spacing: { before: 240, after: 120 },
        indent: INDENT_PARA,
        children: [
          new TextRun({ text: `${m[1]}.`, ...base, bold: true }),
          new TextRun({ text: '  ', ...base }),
          ...parseInline(m[2], { ...base, bold: true }),
        ],
      }));

    // Подпункт (a), (b), (c) — с отступом слева
    } else if ((m = line.match(/^\(([a-zа-я])\)\s+(.+)/i))) {
      result.push(new Paragraph({
        alignment: AlignmentType.BOTH,
        spacing: { ...LINE_150, after: 80 },
        indent: { left: SUBITEM_INDENT, hanging: SUBITEM_INDENT },
        children: [
          new TextRun({ text: `(${m[1]})  `, ...base }),
          ...parseInline(m[2], base),
        ],
      }));

    // Нумерованный пункт N.M. (1.1., 2.3 и т.п.)
    } else if ((m = line.match(/^(\d+\.\d+\.?\s+)(.*)/))) {
      result.push(new Paragraph({
        alignment: AlignmentType.BOTH,
        spacing: { ...LINE_150, after: 120 },
        indent: INDENT_PARA,
        children: [
          new TextRun({ text: m[1], font: FONT, size: SIZE, bold: false }),
          ...parseInline(m[2], base),
        ],
      }));

    // Сквозной нумерованный пункт N. (1., 2., 12., но НЕ «1.1.»)
    } else if ((m = line.match(/^(\d+)\.\s+(.+)/)) && !/^\d+\.\d+/.test(line)) {
      result.push(new Paragraph({
        alignment: AlignmentType.BOTH,
        spacing: { ...LINE_150, after: 120 },
        indent: INDENT_PARA,
        children: [
          new TextRun({ text: `${m[1]}.  `, ...base }),
          ...parseInline(m[2], base),
        ],
      }));

    // Маркированный список
    } else if ((m = line.match(/^[-•*]\s+(.+)/))) {
      result.push(new Paragraph({
        bullet: { level: 0 },
        spacing: { ...LINE_150, after: 60 },
        children: parseInline(m[1], base),
      }));

    // Пустая строка
    } else if (!line.trim()) {
      result.push(new Paragraph({ text: '', spacing: { after: 60 } }));

    // Обычный абзац
    } else {
      result.push(new Paragraph({
        alignment: AlignmentType.BOTH,
        spacing: { ...LINE_150, after: 120 },
        indent: INDENT_PARA,
        children: parseInline(line, base),
      }));
    }
  }
  return result;
}

// ── Экспорт документа (режим «Документы») — ГОСТ-форматирование ───────────────
// Извлекает текст документа между маркерами <!-- DOC_START --> и <!-- DOC_END -->
// Если маркеров нет — возвращает исходный текст целиком.
function extractDocumentContent(content) {
  const re = /<!--\s*DOC_START\s*-->([\s\S]*?)<!--\s*DOC_END\s*-->/i;
  const m = content.match(re);
  if (m && m[1].trim()) return m[1].trim();
  return content;
}

export async function exportMessageToDocx(rawContent) {
  const content = extractDocumentContent(rawContent).replace(/<br\s*\/?>/gi, '\n');
  const lines = content.split('\n');

  // Определяем заголовок для имени файла
  const titleMatch = content.match(/^#+ (.+)/m);
  const title = titleMatch
    ? titleMatch[1].replace(/\*\*/g, '').trim()
    : 'Документ';

  // Разделяем основной текст и блок подписей
  const { body, sig } = splitSignature(lines);

  // Строим параграфы основного текста
  const bodyParagraphs = buildLegalParagraphs(body);

  // Пустой отступ перед подписями
  const spacer = new Paragraph({ text: '', spacing: { before: 480 } });

  // Блок подписей
  const sigBlock = sig.length > 0 ? [spacer, buildSignatureTable(sig)] : [];

  const doc = new Document({
    styles: {
      default: {
        document: {
          run: { font: FONT, size: SIZE },
          paragraph: { spacing: LINE_150 },
        },
      },
    },
    sections: [{
      properties: { page: { size: PAGE_A4, margin: MARGINS } },
      children: [...bodyParagraphs, ...sigBlock],
    }],
  });

  const blob = await Packer.toBlob(doc);
  downloadBlob(blob, `${safeFilename(title)}_${stampSuffix()}.docx`);
}

// ── Экспорт чата → .docx ──────────────────────────────────────────────────────
// Собирает Word-файл истории чата и возвращает Blob (без скачивания).
// Используется и для кнопки «Word», и для вложения в задачу Битрикс.
export async function buildChatDocxBlob(messages, modeLabel) {
  const children = [];
  const base = { size: 22, font: 'Arial' };

  children.push(new Paragraph({
    heading: HeadingLevel.HEADING_1,
    alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: `Чат — ${modeLabel}`, font: 'Arial', bold: true })],
    spacing: { after: 80 },
  }));
  children.push(new Paragraph({
    alignment: AlignmentType.CENTER,
    children: [new TextRun({ text: todayRu(), size: 20, font: 'Arial', color: '888888' })],
    spacing: { after: 200 },
  }));
  children.push(new Paragraph({
    text: '',
    border: { bottom: { style: BorderStyle.SINGLE, size: 2, color: '1e6fc0' } },
    spacing: { after: 240 },
  }));

  for (const msg of messages) {
    const isUser = msg.role === 'user';
    children.push(new Paragraph({
      children: [new TextRun({
        text: isUser ? 'Пользователь' : 'Ассистент',
        bold: true, size: 20, font: 'Arial',
        color: isUser ? '374151' : '1e6fc0',
      })],
      spacing: { before: 320, after: 60 },
    }));

    // Карточки контрагента (только для сообщений ассистента)
    if (!isUser && msg.checkResult?.blocks) {
      const cpBlock  = msg.checkResult.blocks.find(b => b.type === 'counterparty');
      const assBlock = msg.checkResult.blocks.find(b => b.type === 'legalAssessment');
      if (cpBlock)  children.push(...buildCounterpartyDocxRows(cpBlock.data));
      if (assBlock) children.push(...buildAssessmentDocxRows(assBlock.data));
    }

    if (isUser) {
      children.push(new Paragraph({
        children: parseInline(msg.content, base),
        spacing: { after: 100 },
      }));
    } else {
      const cleaned = msg.content
        .replace(/<!--\s*DOC_(START|END)\s*-->/gi, '')
        .replace(/<br\s*\/?>/gi, '\n');
      children.push(...buildLegalParagraphs(cleaned.split('\n'), CONTENT_WIDTH_CHAT));
    }
    children.push(new Paragraph({
      text: '',
      border: { bottom: { style: BorderStyle.SINGLE, size: 1, color: 'E5E7EB' } },
      spacing: { before: 100, after: 100 },
    }));
  }

  const doc = new Document({
    styles: { default: { document: { run: { font: 'Arial', size: 22 } } } },
    sections: [{
      properties: { page: { size: PAGE_A4, margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 } } },
      children,
    }],
  });

  return await Packer.toBlob(doc);
}

// Имя файла для Word-выгрузки/вложения истории чата.
export function chatDocxFilename(modeLabel) {
  return `чат_${safeFilename(modeLabel)}_${dateSuffix()}.docx`;
}

export async function exportChatToDocx(messages, modeLabel) {
  const blob = await buildChatDocxBlob(messages, modeLabel);
  downloadBlob(blob, chatDocxFilename(modeLabel));
}

// ── Экспорт чата → PDF (через диалог печати браузера) ────────────────────────
function inlineHtml(text) {
  return text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\*\*([^*\n]+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*([^*\n]+?)\*/g, '<em>$1</em>')
    .replace(/`([^`\n]+?)`/g, '<code>$1</code>');
}
// Текст ячейки: экранируем + inline-разметка, перенос <br> сохраняем как тег.
function cellHtml(text) {
  return (text || '')
    .split(/<br\s*\/?>/gi)
    .map(part => inlineHtml(part.trim()))
    .join('<br>');
}

function mdToHtml(md) {
  if (!md) return '';
  const lines = md.split('\n');
  const out = [];
  let inUl = false;
  const closeList = () => { if (inUl) { out.push('</ul>'); inUl = false; } };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    let m;

    // Markdown-таблица: собрать подряд идущие строки «| ... |»
    const cells = parseTableRow(line);
    if (cells) {
      closeList();
      const rowsData = [cells];
      let j = i + 1;
      if (j < lines.length && isTableSeparator(lines[j])) j++;
      while (j < lines.length) {
        const next = parseTableRow(lines[j]);
        if (!next) break;
        if (isTableSeparator(lines[j])) { j++; continue; }
        rowsData.push(next);
        j++;
      }
      out.push('<table class="md">');
      rowsData.forEach((row, ri) => {
        const tag = ri === 0 ? 'th' : 'td';
        out.push('<tr>' + row.map(c => `<${tag}>${cellHtml(c)}</${tag}>`).join('') + '</tr>');
      });
      out.push('</table>');
      i = j - 1;
      continue;
    }

    if ((m = line.match(/^### (.+)/)))      { closeList(); out.push(`<h3>${inlineHtml(m[1])}</h3>`); }
    else if ((m = line.match(/^## (.+)/)))  { closeList(); out.push(`<h2>${inlineHtml(m[1])}</h2>`); }
    else if ((m = line.match(/^# (.+)/)))   { closeList(); out.push(`<h1>${inlineHtml(m[1])}</h1>`); }
    else if (line.match(/^---+$/))          { closeList(); out.push('<hr>'); }
    else if ((m = line.match(/^[-*] (.+)/))) {
      if (!inUl) { out.push('<ul>'); inUl = true; }
      out.push(`<li>${inlineHtml(m[1])}</li>`);
    } else if (!line.trim()) { closeList(); out.push('<div class="sp"></div>'); }
    else { closeList(); out.push(`<p>${inlineHtml(line)}</p>`); }
  }
  closeList();
  return out.join('\n');
}

export function exportChatToPdf(messages, modeLabel) {
  const date = todayRu();
  const body = messages.map(msg => {
    const isUser = msg.role === 'user';
    let cards = '';
    if (!isUser && msg.checkResult?.blocks) {
      const cpBlock  = msg.checkResult.blocks.find(b => b.type === 'counterparty');
      const assBlock = msg.checkResult.blocks.find(b => b.type === 'legalAssessment');
      if (cpBlock)  cards += buildCounterpartyHtml(cpBlock.data);
      if (assBlock) cards += buildAssessmentHtml(assBlock.data);
    }
    return `<div class="msg ${isUser ? 'usr' : 'ast'}">
      <div class="lbl">${isUser ? 'Пользователь' : 'Ассистент'}</div>
      <div class="cnt">${cards}${isUser ? `<p>${inlineHtml(msg.content)}</p>` : mdToHtml(msg.content)}</div>
    </div>`;
  }).join('');

  const html = `<!DOCTYPE html>
<html lang="ru"><head>
<meta charset="UTF-8">
<title>Чат — ${modeLabel} — ${date}</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Arial,sans-serif;font-size:12pt;color:#111;padding:32px;max-width:820px;margin:0 auto}
.hdr{border-bottom:3px solid #1e6fc0;padding-bottom:12px;margin-bottom:28px}
.hdr h1{font-size:17pt;color:#0b2a5b}
.hdr .dt{font-size:10pt;color:#888;margin-top:4px}
.msg{margin-bottom:20px;padding-bottom:16px;border-bottom:1px solid #e5e7eb}
.lbl{font-size:9pt;font-weight:700;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px}
.usr .lbl{color:#555}.ast .lbl{color:#1e6fc0}
.cnt{line-height:1.65}
.cnt p{margin:5px 0}.cnt h1{font-size:15pt;margin:14px 0 5px}
.cnt h2{font-size:13pt;margin:12px 0 4px}.cnt h3{font-size:11pt;margin:10px 0 4px}
.cnt ul{padding-left:22px;margin:6px 0}.cnt li{margin:3px 0}
.cnt hr{border:none;border-top:1px solid #ccc;margin:12px 0}
.cnt code{font-family:'Courier New',monospace;font-size:10pt;background:#f3f4f6;padding:1px 4px;border-radius:3px}
.cnt table.md{border-collapse:collapse;width:100%;margin:8px 0;font-size:10.5pt}
.cnt table.md th,.cnt table.md td{border:1px solid #999;padding:4px 7px;text-align:left;vertical-align:top}
.cnt table.md th{background:#f3f4f6;font-weight:700}
.cnt table.md tr{page-break-inside:avoid}
.sp{height:7px}
.ccard{border:1px solid #d1d5db;border-radius:6px;padding:10px 14px;margin:8px 0 12px;background:#f9fafb;font-size:10.5pt}
.ccard h3{font-size:11pt;color:#1e6fc0;margin:0 0 8px;padding-bottom:4px;border-bottom:1px solid #e5e7eb}
.ctbl{border-collapse:collapse;width:100%;margin-bottom:8px}
.ctbl td{padding:3px 6px;font-size:10pt;vertical-align:top}
.ctbl .cl{color:#555;font-weight:600;width:40%;white-space:nowrap}
.ccard ul{margin:4px 0;padding-left:18px}.ccard li{margin:2px 0}
.lim{color:#b45309;margin:4px 0}
.disc{color:#888;font-style:italic;font-size:9.5pt;margin-top:6px;border-top:1px solid #e5e7eb;padding-top:4px}
@media print{body{padding:0}.msg{page-break-inside:avoid}.ccard{page-break-inside:avoid}}
</style></head><body>
<div class="hdr"><h1>Чат — ${modeLabel}</h1><div class="dt">${date}</div></div>
${body}</body></html>`;

  const blob = new Blob([html], { type: 'text/html;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const win = window.open(url, '_blank');
  if (win) {
    win.addEventListener('load', () => {
      setTimeout(() => { win.print(); setTimeout(() => URL.revokeObjectURL(url), 60000); }, 400);
    });
  } else {
    URL.revokeObjectURL(url);
    alert('Разрешите всплывающие окна для этого сайта');
  }
}
