import { Link } from 'react-router-dom';
import type { Issue } from '../api/types';

type IssueListProps = {
  issues: Issue[];
  isRead: (issue: Issue) => boolean;
  onToggleRead: (issue: Issue, read: boolean) => void;
  onDeleteIssue: (issue: Issue) => void;
};

export function IssueList({ issues, isRead, onToggleRead, onDeleteIssue }: IssueListProps) {
  return (
    <div className="issue-list">
      {issues.map((issue) => (
        <article key={issue.id} className="issue-row">
          <Link to={`/viewer/${issue.id}`} className="issue-row__cover-link">
            {issue.coverUrl ? (
              <img src={issue.coverUrl} alt={`${issue.title} cover`} className="issue-row__cover" loading="lazy" />
            ) : (
              <div className="issue-row__cover issue-row__cover--placeholder" />
            )}
          </Link>
          <div className="issue-row__body">
            <Link to={`/viewer/${issue.id}`} className="issue-row__content">
              <p className="eyebrow">Issue {issue.number}</p>
              <h4>{issue.title}</h4>
              <p>{issue.summary}</p>
            </Link>
          </div>
          <div className="issue-row__meta">
            <span>{issue.releaseDate}</span>
            <span>{issue.pageCount} pages</span>
            <label className="issue-row__checkbox">
              <input
                type="checkbox"
                checked={isRead(issue)}
                onChange={(event) => onToggleRead(issue, event.target.checked)}
              />
              <span>Read</span>
            </label>
            <button type="button" className="button" onClick={() => onDeleteIssue(issue)}>
              Delete issue
            </button>
          </div>
        </article>
      ))}
    </div>
  );
}
