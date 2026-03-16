import { Building2, Building, FolderOpen, MessageSquare, FileText, Settings, LogOut } from 'lucide-react';
import { useAuth } from '../modules/auth/AuthContext';

interface SidebarProps {
  activeView: 'buildings' | 'files' | 'assistant' | 'documents' | 'settings';
  onNavigate: (view: 'buildings' | 'files' | 'assistant' | 'documents' | 'settings') => void;
  user?: any;
}

export function Sidebar({ activeView, onNavigate, user }: SidebarProps) {
  const { logout, profile } = useAuth();
  
  const navItems = [
    { id: 'buildings' as const, icon: Building, label: 'Buildings' },
    { id: 'files' as const, icon: FolderOpen, label: 'File Management' },
    { id: 'documents' as const, icon: FileText, label: 'Documents' },
    { id: 'assistant' as const, icon: MessageSquare, label: 'AI Assistant' },
    { id: 'settings' as const, icon: Settings, label: 'Settings' },
  ];

  // Get user display info
  const displayName = profile?.name || user?.displayName || user?.email || 'User';
  const userRole = profile?.role || 'Member';
  const userCompany = profile?.company || '';
  const initials = displayName.split(' ').map((n: string) => n[0]).join('').toUpperCase().slice(0, 2);

  return (
    <aside className="w-64 bg-white border-r border-gray-200 flex flex-col">
      <div className="p-6 border-b border-gray-200">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 bg-gradient-to-br from-blue-600 to-cyan-500 rounded-lg flex items-center justify-center">
            <Building2 className="w-6 h-6 text-white" />
          </div>
          <div>
            <h1 className="text-gray-900 font-semibold">BuildingOS</h1>
            <p className="text-sm text-gray-500">AI Platform</p>
          </div>
        </div>
      </div>

      <nav className="flex-1 p-4 overflow-y-auto">
        <ul className="space-y-1">
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = activeView === item.id;
            
            return (
              <li key={item.id}>
                <button
                  onClick={() => onNavigate(item.id)}
                  className={`w-full flex items-center gap-3 px-4 py-3 rounded-lg transition-colors ${
                    isActive
                      ? 'bg-blue-50 text-blue-600'
                      : 'text-gray-700 hover:bg-gray-50'
                  }`}
                >
                  <Icon className="w-5 h-5 flex-shrink-0" />
                  <span className="text-sm flex-1 text-left">{item.label}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </nav>

      {/* User Profile Section */}
      <div className="p-4 border-t border-gray-200">
        <div className="flex items-center gap-3 mb-3">
          <div className="w-10 h-10 bg-blue-100 rounded-full flex items-center justify-center">
            <span className="text-blue-600 font-medium text-sm">{initials}</span>
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-gray-900 truncate">{displayName}</p>
            <p className="text-xs text-gray-500 truncate">{userRole}</p>
          </div>
        </div>
        {userCompany && (
          <p className="text-xs text-gray-400 mb-3 truncate">{userCompany}</p>
        )}
        <button
          onClick={logout}
          className="w-full flex items-center gap-2 px-3 py-2 text-sm text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
        >
          <LogOut className="w-4 h-4" />
          Sign Out
        </button>
      </div>
    </aside>
  );
}
