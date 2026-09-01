import { useState } from 'react';
import { Navigate, Route, Routes, useParams } from 'react-router-dom';
import { AuthGate } from './components/AuthGate';
import { Shell } from './components/Shell';
import { CataloguePage } from './routes/CataloguePage';
import { ChronologyPage } from './routes/ChronologyPage';
import { ListsPage } from './routes/ListsPage';
import { ReadingPathDetailPage } from './routes/ReadingPathDetailPage';
import { SeriesDetailPage } from './routes/SeriesDetailPage';
import { ViewerPage } from './routes/ViewerPage';

/** Collection detail moved from /all/:id to /collections/:id. */
function LegacyCollectionRedirect() {
  const { readingPathId } = useParams();
  return <Navigate to={`/collections/${readingPathId}`} replace />;
}

export default function App() {
  const [libraryRefreshToken, setLibraryRefreshToken] = useState(0);
  const [searchQuery, setSearchQuery] = useState('');

  function markLibraryDirty() {
    setLibraryRefreshToken((value) => value + 1);
  }

  return (
    <AuthGate>
      <Shell searchQuery={searchQuery} onSearchChange={setSearchQuery}>
        <Routes>
          <Route path="/" element={<Navigate to="/catalogue" replace />} />
          <Route
            path="/catalogue"
            element={<CataloguePage searchQuery={searchQuery} refreshToken={libraryRefreshToken} />}
          />
          <Route path="/chronology" element={<ChronologyPage searchQuery={searchQuery} />} />
          <Route path="/lists" element={<ListsPage />} />
          <Route
            path="/collections/:readingPathId"
            element={<ReadingPathDetailPage onLibraryMutated={markLibraryDirty} />}
          />
          <Route path="/series/:seriesId" element={<SeriesDetailPage />} />
          <Route path="/viewer/:issueId" element={<ViewerPage />} />
          <Route path="/viewer/canonical/:canonicalIssueId" element={<ViewerPage />} />
          <Route path="/viewer/reading-path/:readingPathId/entries/:entryId" element={<ViewerPage />} />

          <Route path="/library" element={<Navigate to="/catalogue" replace />} />
          <Route path="/all" element={<Navigate to="/catalogue" replace />} />
          <Route path="/all/:readingPathId" element={<LegacyCollectionRedirect />} />
          <Route path="/reading-paths" element={<Navigate to="/catalogue" replace />} />
          <Route path="/reading-paths/:readingPathId" element={<LegacyCollectionRedirect />} />
          <Route path="/flight-prep" element={<Navigate to="/lists" replace />} />
          <Route path="*" element={<Navigate to="/catalogue" replace />} />
        </Routes>
      </Shell>
    </AuthGate>
  );
}
