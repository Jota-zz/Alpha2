/**
 * Sidebar con 5 entradas. Usa <NavLink> de react-router-dom para
 * que el link activo se marque automáticamente.
 *
 * El "brand" arriba es un dot verde lima (accent del HTML adjunto) +
 * el nombre del producto.
 */

import { NavLink } from 'react-router-dom';
import {
  LayoutDashboard,
  BarChart3,
  Settings,
  Terminal,
} from 'lucide-react';

const LINKS = [
  { to: '/', label: 'Panel de control', icon: LayoutDashboard, end: true },
  { to: '/argos', label: 'Dashboard', icon: BarChart3 },
  { to: '/configuracion', label: 'Configuración', icon: Settings },
  { to: '/logs', label: 'Logs', icon: Terminal },
];

export function Sidebar() {
  return (
    <aside className="sidebar">
      <div className="sidebar-brand">
        <div className="sidebar-brand-dot" />
        <span>Alpha Bot</span>
      </div>

      <nav>
        {LINKS.map((link) => (
          <NavLink
            key={link.to}
            to={link.to}
            end={link.end}
            className={({ isActive }) =>
              isActive ? 'sidebar-link active' : 'sidebar-link'
            }
          >
            <link.icon size={18} strokeWidth={2} />
            <span>{link.label}</span>
          </NavLink>
        ))}
      </nav>

      <div className="mt-auto pt-6 px-2 text-[0.7rem] text-slate-500">
        v1.4.3
      </div>
    </aside>
  );
}
