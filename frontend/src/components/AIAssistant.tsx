import { useState, useEffect, useRef } from 'react';
import { Send, Bot, User, Sparkles, Building, Check, Loader2, RefreshCw } from 'lucide-react';
import { auth } from '../lib/firebase';
import { API_URL } from '../lib/api';
import { useAuth } from '../modules/auth/AuthContext';

interface Building {
  id: string;
  name: string;
}

interface Message {
  role: 'user' | 'assistant';
  content: string;
}

export function AIAssistant() {
  const { profile } = useAuth();
  const [messages, setMessages] = useState<Message[]>([
    {
      role: 'assistant',
      content:
        "Hello! I'm your BuildingOS AI Assistant. Select one or more buildings above, and I'll help you with questions about their maintenance, equipment, documents, and more.\n\nWhat would you like help with today?",
    },
  ]);
  const [input, setInput] = useState('');
  const [buildings, setBuildings] = useState<Building[]>([]);
  const [selectedBuildings, setSelectedBuildings] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingBuildings, setLoadingBuildings] = useState(true);
  const [isIndexing, setIsIndexing] = useState(false);
  const [indexStatus, setIndexStatus] = useState<string>('');
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Fetch buildings (wait for profile to load, scoped to company)
  useEffect(() => {
    if (!profile?.company) return;
    
    const fetchBuildings = async () => {
      try {
        setLoadingBuildings(true);
        const response = await fetch(`${API_URL}/_api/buildings?companyId=${encodeURIComponent(profile.company)}`);
        if (response.ok) {
          const data = await response.json();
          setBuildings(data || []);
        }
      } catch (error) {
        console.error('Error fetching buildings:', error);
      } finally {
        setLoadingBuildings(false);
      }
    };
    fetchBuildings();
    
    // Refresh every 30 seconds
    const interval = setInterval(fetchBuildings, 30000);
    return () => clearInterval(interval);
  }, [profile?.company]);

  // Auto-scroll to bottom when messages change
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const toggleBuilding = (buildingId: string) => {
    setSelectedBuildings(prev =>
      prev.includes(buildingId)
        ? prev.filter(id => id !== buildingId)
        : [...prev, buildingId]
    );
  };

  const selectAllBuildings = () => {
    if (selectedBuildings.length === buildings.length) {
      setSelectedBuildings([]);
    } else {
      setSelectedBuildings(buildings.map(b => b.id));
    }
  };

  const getSelectedBuildingNames = () => {
    return buildings
      .filter(b => selectedBuildings.includes(b.id))
      .map(b => b.name);
  };

  // Sync and index documents for selected buildings
  const handleSyncDocuments = async () => {
    if (selectedBuildings.length === 0) {
      setIndexStatus('⚠️ Select buildings first');
      setTimeout(() => setIndexStatus(''), 3000);
      return;
    }

    setIsIndexing(true);
    setIndexStatus('Indexing documents...');
    
    const buildingNames = getSelectedBuildingNames();
    let totalProcessed = 0;
    let errors: string[] = [];

    try {
      for (const buildingName of buildingNames) {
        setIndexStatus(`Indexing ${buildingName}...`);
        
        const response = await fetch(`${API_URL}/_api/ai/vectors/sync/${encodeURIComponent(buildingName)}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' }
        });

        const result = await response.json();
        
        if (result.success) {
          totalProcessed += result.summary?.processed || 0;
        } else {
          errors.push(`${buildingName}: ${result.summary?.error || 'Failed'}`);
        }
      }

      if (errors.length > 0) {
        setIndexStatus(`⚠️ Errors: ${errors.join(', ')}`);
      } else if (totalProcessed > 0) {
        setIndexStatus(`✓ Indexed ${totalProcessed} document chunks from ${buildingNames.length} building(s)`);
      } else {
        setIndexStatus(`✓ No new documents to index`);
      }
    } catch (error) {
      console.error('Sync error:', error);
      setIndexStatus('❌ Sync failed. Check console.');
    } finally {
      setIsIndexing(false);
      setTimeout(() => setIndexStatus(''), 5000);
    }
  };

  const handleSend = async () => {
    if (!input.trim()) return;

    const userMessage: Message = { role: 'user', content: input };
    setMessages(prev => [...prev, userMessage]);
    setInput('');
    setLoading(true);

    try {
      const user = auth.currentUser;
      const token = user ? await user.getIdToken() : null;

      const conversationHistory = messages
        .filter((_, idx) => idx > 0) // Skip welcome message
        .map(m => ({
          role: m.role,
          content: m.content
        }));

      const response = await fetch(`${API_URL}/_api/ai/chat`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token && { 'Authorization': `Bearer ${token}` })
        },
        body: JSON.stringify({
          message: input,
          conversationHistory,
          selectedBuildings: getSelectedBuildingNames()
        })
      });

      const data = await response.json();

      if (data.success && data.response) {
        setMessages(prev => [...prev, { role: 'assistant', content: data.response }]);
      } else {
        setMessages(prev => [...prev, { 
          role: 'assistant', 
          content: data.response || 'Sorry, I encountered an error. Please try again.' 
        }]);
      }
    } catch (error) {
      console.error('AI Chat error:', error);
      setMessages(prev => [...prev, { 
        role: 'assistant', 
        content: 'Sorry, I encountered a connection error. Please try again.' 
      }]);
    } finally {
      setLoading(false);
    }
  };

  const suggestedQuestions = [
    'What is the current status of this building?',
    'Show me recent maintenance activities',
    'What equipment needs attention?',
    'Summarize the building specifications',
  ];

  const handleQuestionClick = (question: string) => {
    setInput(question);
  };

  return (
    <div className="h-full flex flex-col max-w-5xl mx-auto p-8">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900 mb-2">AI Assistant</h1>
        <p className="text-gray-600">
          Select buildings to ask questions about their documents, equipment, and maintenance
        </p>
      </div>

      {/* Building Selection */}
      <div className="mb-6">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Building className="w-5 h-5 text-blue-600" />
            <span className="font-medium text-gray-900">Select Buildings</span>
            {selectedBuildings.length > 0 && (
              <span className="text-sm text-gray-500">
                ({selectedBuildings.length} selected)
              </span>
            )}
          </div>
          <div className="flex items-center gap-3">
            {buildings.length > 1 && (
              <button
                onClick={selectAllBuildings}
                className="text-sm text-blue-600 hover:text-blue-700"
              >
                {selectedBuildings.length === buildings.length ? 'Deselect All' : 'Select All'}
              </button>
            )}
            <div className="flex items-center gap-2">
              <button
                onClick={handleSyncDocuments}
                disabled={isIndexing || selectedBuildings.length === 0}
                className="flex items-center gap-2 px-3 py-1.5 text-sm bg-purple-600 text-white rounded-lg hover:bg-purple-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                title="Index PDF documents for AI to read"
              >
                <RefreshCw className={`w-4 h-4 ${isIndexing ? 'animate-spin' : ''}`} />
                {isIndexing ? 'Indexing...' : 'Sync & Index'}
              </button>
              <span className="text-xs text-gray-500 italic">Click after uploading new files</span>
            </div>
          </div>
        </div>

        {indexStatus && (
          <div className={`mb-3 p-2 rounded-lg text-sm ${
            indexStatus.startsWith('✓') ? 'bg-green-50 text-green-700 border border-green-200' :
            indexStatus.startsWith('⚠️') ? 'bg-yellow-50 text-yellow-700 border border-yellow-200' :
            indexStatus.startsWith('❌') ? 'bg-red-50 text-red-700 border border-red-200' :
            'bg-blue-50 text-blue-700 border border-blue-200'
          }`}>
            {indexStatus}
          </div>
        )}

        {loadingBuildings ? (
          <div className="flex items-center gap-2 text-gray-500">
            <Loader2 className="w-4 h-4 animate-spin" />
            <span>Loading buildings...</span>
          </div>
        ) : buildings.length === 0 ? (
          <div className="bg-gray-50 rounded-xl p-4 text-center text-gray-500">
            No buildings found. Add buildings in the Buildings tab first.
          </div>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
            {buildings.map(building => {
              const isSelected = selectedBuildings.includes(building.id);
              return (
                <button
                  key={building.id}
                  onClick={() => toggleBuilding(building.id)}
                  className={`relative p-4 rounded-xl border-2 transition-all text-left ${
                    isSelected
                      ? 'border-blue-500 bg-blue-50'
                      : 'border-gray-200 bg-white hover:border-gray-300'
                  }`}
                >
                  {isSelected && (
                    <div className="absolute top-2 right-2 w-5 h-5 bg-blue-500 rounded-full flex items-center justify-center">
                      <Check className="w-3 h-3 text-white" />
                    </div>
                  )}
                  <div className="flex items-center gap-3">
                    <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${
                      isSelected ? 'bg-blue-500' : 'bg-gray-100'
                    }`}>
                      <Building className={`w-5 h-5 ${isSelected ? 'text-white' : 'text-gray-600'}`} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className={`font-medium truncate ${isSelected ? 'text-blue-900' : 'text-gray-900'}`}>
                        {building.name}
                      </p>
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Chat Container */}
      <div className="flex-1 bg-white rounded-xl border border-gray-200 flex flex-col overflow-hidden shadow-sm">
        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {messages.map((message, index) => (
            <div
              key={index}
              className={`flex gap-4 ${message.role === 'user' ? 'flex-row-reverse' : ''}`}
            >
              <div
                className={`w-10 h-10 rounded-full flex items-center justify-center flex-shrink-0 ${
                  message.role === 'user'
                    ? 'bg-blue-600'
                    : 'bg-gradient-to-br from-purple-600 to-pink-500'
                }`}
              >
                {message.role === 'user' ? (
                  <User className="w-5 h-5 text-white" />
                ) : (
                  <Bot className="w-5 h-5 text-white" />
                )}
              </div>
              <div className={`flex-1 ${message.role === 'user' ? 'flex justify-end' : ''}`}>
                <div
                  className={`inline-block max-w-[85%] p-4 rounded-2xl ${
                    message.role === 'user'
                      ? 'bg-blue-600 text-white'
                      : 'bg-gray-100 text-gray-900'
                  }`}
                >
                  <p className="text-sm leading-relaxed whitespace-pre-wrap">
                    {message.content}
                  </p>
                </div>
              </div>
            </div>
          ))}

          {loading && (
            <div className="flex gap-4">
              <div className="w-10 h-10 rounded-full flex items-center justify-center bg-gradient-to-br from-purple-600 to-pink-500">
                <Bot className="w-5 h-5 text-white" />
              </div>
              <div className="flex-1">
                <div className="inline-block p-4 rounded-2xl bg-gray-100">
                  <div className="flex items-center gap-2">
                    <Loader2 className="w-4 h-4 animate-spin text-gray-600" />
                    <span className="text-sm text-gray-600">Thinking...</span>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Suggested Questions */}
          {messages.length === 1 && selectedBuildings.length > 0 && (
            <div className="pt-4">
              <p className="text-sm text-gray-600 mb-3 flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-purple-600" />
                Try asking me about:
              </p>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {suggestedQuestions.map((question, index) => (
                  <button
                    key={index}
                    onClick={() => handleQuestionClick(question)}
                    className="text-left p-4 bg-gradient-to-br from-blue-50 to-purple-50 border border-blue-200 rounded-xl hover:border-blue-300 hover:shadow-sm transition-all"
                  >
                    <div className="flex items-start gap-2">
                      <Sparkles className="w-4 h-4 text-blue-600 flex-shrink-0 mt-0.5" />
                      <span className="text-sm text-gray-700">{question}</span>
                    </div>
                  </button>
                ))}
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Input */}
        <div className="border-t border-gray-200 p-4 bg-gray-50">
          {selectedBuildings.length === 0 && (
            <div className="mb-3 p-3 bg-yellow-50 border border-yellow-200 rounded-lg text-sm text-yellow-800">
              ⚠️ Please select at least one building above to start asking questions.
            </div>
          )}
          <div className="flex gap-3">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyPress={(e) => e.key === 'Enter' && !loading && handleSend()}
              placeholder={selectedBuildings.length > 0 
                ? "Ask about maintenance, equipment, documents..." 
                : "Select a building first..."
              }
              disabled={selectedBuildings.length === 0 || loading}
              className="flex-1 px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white disabled:bg-gray-100 disabled:cursor-not-allowed"
            />
            <button
              onClick={handleSend}
              disabled={!input.trim() || selectedBuildings.length === 0 || loading}
              className="px-6 py-3 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
            >
              {loading ? (
                <Loader2 className="w-5 h-5 animate-spin" />
              ) : (
                <Send className="w-5 h-5" />
              )}
              Send
            </button>
          </div>
          <div className="flex items-center justify-between mt-3">
            <p className="text-xs text-gray-500">
              {selectedBuildings.length > 0 
                ? `AI context: ${getSelectedBuildingNames().join(', ')}`
                : 'No buildings selected'
              }
            </p>
            <div className="flex items-center gap-2 text-xs text-gray-500">
              <div className="w-2 h-2 bg-green-500 rounded-full animate-pulse" />
              <span>Powered by OpenAI</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
