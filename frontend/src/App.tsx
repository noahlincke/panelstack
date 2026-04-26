import { useState } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { AuthGate } from './components/AuthGate';
import { Shell } from './components/Shell';
import { LibraryPage } from './routes/LibraryPage';
import { ReadingPathDetailPage } from './routes/ReadingPathDetailPage';
import { ReadingPathsPage } from './routes/ReadingPathsPage';
import { SeriesDetailPage } from './routes/SeriesDetailPage';
import { ViewerPage } from './routes/ViewerPage';

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
          <Route path="/" element={<Navigate to="/library" replace />} />
          <Route
            path="/all"
            element={<ReadingPathsPage onLibraryMutated={markLibraryDirty} searchQuery={searchQuery} />}
          />
          <Route path="/all/:readingPathId" element={<ReadingPathDetailPage onLibraryMutated={markLibraryDirty} />} />
          <Route path="/reading-paths" element={<Navigate to="/all" replace />} />
          <Route path="/reading-paths/:readingPathId" element={<Navigate to="/all" replace />} />
          <Route
            path="/library"
            element={<LibraryPage refreshToken={libraryRefreshToken} searchQuery={searchQuery} onLibraryMutated={markLibraryDirty} />}
          />
          <Route path="/series/:seriesId" element={<SeriesDetailPage />} />
          <Route path="/viewer/:issueId" element={<ViewerPage />} />
          <Route path="/viewer/canonical/:canonicalIssueId" element={<ViewerPage />} />
          <Route path="*" element={<Navigate to="/all" replace />} />
        </Routes>
      </Shell>
    </AuthGate>
  );
}
