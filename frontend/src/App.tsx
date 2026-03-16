import { useState } from 'react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { BuildingView } from './components/BuildingView';
import { FileManagement } from './components/FileManagement';
import { AIAssistant } from './components/AIAssistant';
import { DocumentAnalysis } from './components/DocumentAnalysis';
import { Sidebar } from './components/Sidebar';
import { ErrorBoundary } from './components/ErrorBoundary';
import { AuthProvider, useAuth, LoginPage, InvitePage } from './modules/auth';
import { SettingsPage } from './modules/settings';

function AppContent() {
  const { user, loading } = useAuth();
  const [activeView, setActiveView] = useState<'buildings' | 'files' | 'assistant' | 'documents' | 'settings'>(
    () => (localStorage.getItem('activeView') as any) || 'buildings'
  );

  const handleNavigate = (view: typeof activeView) => {
    setActiveView(view);
    localStorage.setItem('activeView', view);
  };

  if (loading) {
    return <div className="flex h-screen items-center justify-center">Loading...</div>;
  }

  if (!user) {
    return <LoginPage />;
  }

  return (
    <div className="flex h-screen bg-gray-50">
      <Sidebar activeView={activeView} onNavigate={handleNavigate} user={user} />

      <main className="flex-1 overflow-auto">
        <div style={{ display: activeView === 'buildings' ? 'block' : 'none', height: '100%' }}>
          <BuildingView />
        </div>
        <div style={{ display: activeView === 'files' ? 'block' : 'none', height: '100%' }}>
          <FileManagement />
        </div>
        {activeView === 'assistant' && <AIAssistant />}
        {activeView === 'documents' && <DocumentAnalysis />}
        {activeView === 'settings' && <SettingsPage />}
      </main>
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <ErrorBoundary>
          <Routes>
            <Route path="/invite/:token" element={<InvitePage />} />
            <Route path="/*" element={<AppContent />} />
          </Routes>
        </ErrorBoundary>
      </AuthProvider>
    </BrowserRouter>
  );
}