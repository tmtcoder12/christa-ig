import { Navigate, Route, Routes } from 'react-router-dom';
import { AppShell } from './components/AppShell';
import { ProtectedRoute } from './components/ProtectedRoute';
import { AuthProvider } from './lib/auth';
import { AddPromotion } from './pages/AddPromotion';
import { Redeem } from './pages/Redeem';
import { SignIn } from './pages/SignIn';
import { SignUp } from './pages/SignUp';

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/signin" element={<SignIn />} />
        <Route path="/signup" element={<SignUp />} />
        <Route element={<ProtectedRoute />}>
          <Route element={<AppShell />}>
            <Route path="/redeem" element={<Redeem />} />
            <Route path="/add-promotion" element={<AddPromotion />} />
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/redeem" replace />} />
      </Routes>
    </AuthProvider>
  );
}
