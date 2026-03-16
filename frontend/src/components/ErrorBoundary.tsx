import React, { Component, ErrorInfo, ReactNode } from 'react';

interface Props {
    children?: ReactNode;
}

interface State {
    hasError: boolean;
    error?: Error;
}

export class ErrorBoundary extends Component<Props, State> {
    public state: State = {
        hasError: false
    };

    public static getDerivedStateFromError(error: Error): State {
        return { hasError: true, error };
    }

    public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
        console.error("Uncaught error:", error, errorInfo);
    }

    public render() {
        if (this.state.hasError) {
            return (
                <div className="flex h-screen items-center justify-center bg-gray-50 p-4">
                    <div className="bg-white p-8 rounded-lg shadow-lg max-w-lg w-full">
                        <h2 className="text-2xl font-bold text-red-600 mb-4">Application Error</h2>
                        <p className="text-gray-600 mb-4">An unexpected error occurred. Please try reloading.</p>
                        <div className="bg-gray-100 p-4 rounded text-sm font-mono overflow-auto max-h-64 mb-6">
                            <p className="font-bold mb-2">{this.state.error?.message}</p>
                            <pre className="whitespace-pre-wrap text-xs text-gray-500">
                                {this.state.error?.stack}
                            </pre>
                        </div>
                        <button
                            className="w-full px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 transition-colors"
                            onClick={() => window.location.reload()}
                        >
                            Reload Page
                        </button>
                    </div>
                </div>
            );
        }

        return this.props.children;
    }
}
