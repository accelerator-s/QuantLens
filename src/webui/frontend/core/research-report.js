import { el } from './dom.js';
import { renderMarkdown } from '../components/chat-stream/chat-markdown.js';

export function createResearchReport(source) {
  const card = el('article', 'card detail-card research-report');
  const head = el('div', 'detail-card__head');
  head.append(el('h4', '', '研究报告'));
  const body = el('div', 'research-report__body');
  const content = (source || '').split('\n').filter(line =>
    !/^本报告不承诺未来收益；“预期”仅指上述固定口径下的历史样本外统计。?$/.test(line.trim()) &&
    !/^\s*(?:[-*+]\s+)?(?:\*\*)?(?:数据快照|固定协议哈希)(?:\*\*)?\s*[：:]/.test(line)
  ).map(line => line.replace(/[；;]\s*(?:数据快照|固定协议哈希)\s*[：:]\s*(?:`[^`]*`|[^\s。；;]+)/g, ''))
    .join('\n').trimEnd();
  renderMarkdown(body, content || '暂无报告内容。');
  card.append(head, body);
  return card;
}
