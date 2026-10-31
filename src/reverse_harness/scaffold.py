import json
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any
from rich.console import Console

from reverse_harness.config import ROOT_DIR
from reverse_harness.dna import get_or_build_archetypes

console = Console()

def scaffold_nextjs_app(
    project_name: str,
    concept: str,
    archetype_key: str = "dark-luxury",
    target_dir: Optional[Path] = None,
) -> Path:
    """Scaffolds a high-craft Next.js 15 application based on reverse-engineered design DNA."""
    out_dir = target_dir or (ROOT_DIR.parent / project_name).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    archetypes = get_or_build_archetypes()
    arch = archetypes.get(archetype_key, archetypes["dark-luxury"])
    colors = arch["colors"]
    typo = arch["typography"]

    console.print(f"[bold cyan]Scaffolding high-craft application:[/bold cyan] [green]{project_name}[/green]")
    console.print(f"[dim]Concept:[/dim] {concept}")
    console.print(f"[dim]Design Archetype:[/dim] [yellow]{arch['name']}[/yellow]")
    console.print(f"[dim]Directory:[/dim] {out_dir}")

    # 1. package.json
    pkg_json = {
        "name": project_name,
        "version": "0.1.0",
        "private": True,
        "scripts": {
            "dev": "next dev",
            "build": "next build",
            "start": "next start",
            "lint": "next lint"
        },
        "dependencies": {
            "next": "^15.1.0",
            "react": "^19.0.0",
            "react-dom": "^19.0.0",
            "lucide-react": "^0.468.0",
            "framer-motion": "^11.13.0",
            "clsx": "^2.1.1",
            "tailwind-merge": "^2.5.5"
        },
        "devDependencies": {
            "typescript": "^5.7.2",
            "@types/node": "^22.10.1",
            "@types/react": "^19.0.1",
            "@types/react-dom": "^19.0.1",
            "postcss": "^8.4.49",
            "tailwindcss": "^3.4.16",
            "autoprefixer": "^10.4.20"
        }
    }
    (out_dir / "package.json").write_text(json.dumps(pkg_json, indent=2), encoding="utf-8")

    # 2. tsconfig.json
    tsconfig = {
        "compilerOptions": {
            "target": "ES2022",
            "lib": ["dom", "dom.iterable", "esnext"],
            "allowJs": True,
            "skipLibCheck": True,
            "strict": True,
            "noEmit": True,
            "esModuleInterop": True,
            "module": "esnext",
            "moduleResolution": "bundler",
            "resolveJsonModule": True,
            "isolatedModules": True,
            "jsx": "preserve",
            "incremental": True,
            "plugins": [{"name": "next"}],
            "paths": {"@/*": ["./*"]}
        },
        "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
        "exclude": ["node_modules"]
    }
    (out_dir / "tsconfig.json").write_text(json.dumps(tsconfig, indent=2), encoding="utf-8")

    # 3. postcss.config.mjs
    (out_dir / "postcss.config.mjs").write_text("""export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
""", encoding="utf-8")

    # 4. tailwind.config.ts with reverse-engineered tokens
    tailwind_cfg = f"""import type {{ Config }} from "tailwindcss";

const config: Config = {{
  content: [
    "./pages/**/*.{{js,ts,jsx,tsx,mdx}}",
    "./components/**/*.{{js,ts,jsx,tsx,mdx}}",
    "./app/**/*.{{js,ts,jsx,tsx,mdx}}",
  ],
  theme: {{
    extend: {{
      colors: {{
        background: "{colors['background']}",
        surface: "{colors['surface']}",
        "surface-elevated": "{colors['surface_elevated']}",
        border: "{colors['border']}",
        "text-primary": "{colors['text_primary']}",
        "text-muted": "{colors['text_muted']}",
        accent: "{colors['accent']}",
        "accent-glow": "{colors['accent_glow']}",
      }},
      letterSpacing: {{
        widest: "{typo['label_tracking']}",
        tighter: "{typo['heading_tracking']}",
      }},
    }},
  }},
  plugins: [],
}};
export default config;
"""
    (out_dir / "tailwind.config.ts").write_text(tailwind_cfg, encoding="utf-8")

    # 5. app directory
    app_dir = out_dir / "app"
    app_dir.mkdir(parents=True, exist_ok=True)

    # globals.css
    globals_css = f"""@tailwind base;
@tailwind components;
@tailwind utilities;

:root {{
  --background: {colors['background']};
  --surface: {colors['surface']};
  --border: {colors['border']};
  --text-primary: {colors['text_primary']};
  --text-muted: {colors['text_muted']};
  --accent: {colors['accent']};
}}

body {{
  background-color: var(--background);
  color: var(--text-primary);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  overflow-x: hidden;
  selection-background-color: var(--accent);
}}

::-webkit-scrollbar {{
  width: 6px;
}}
::-webkit-scrollbar-track {{
  background: var(--background);
}}
::-webkit-scrollbar-thumb {{
  background: var(--border);
  border-radius: 3px;
}}
"""
    (app_dir / "globals.css").write_text(globals_css, encoding="utf-8")

    # layout.tsx
    layout_tsx = f"""import type {{ Metadata }} from "next";
import "./globals.css";

export const metadata: Metadata = {{
  title: "{project_name.title().replace('-', ' ')} — {concept}",
  description: "{concept} — Engineered with high-craft architectural standards.",
}};

export default function RootLayout({{
  children,
}}: Readonly<{{
  children: React.ReactNode;
}}>) {{
  return (
    <html lang="en" className="dark scroll-smooth">
      <body className="min-h-screen bg-background text-text-primary antialiased selection:bg-accent selection:text-background">
        {{children}}
      </body>
    </html>
  );
}}
"""
    (app_dir / "layout.tsx").write_text(layout_tsx, encoding="utf-8")

    # 6. Domain Data: data/projects.ts
    data_dir = out_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    projects_ts = """export interface Project {
  id: string;
  slug: string;
  title: string;
  client: string;
  year: string;
  category: string;
  role: string;
  summary: string;
  heroImage: string;
  featured: boolean;
  metrics?: { label: string; value: string }[];
}

export const PROJECTS: Project[] = [
  {
    id: "1",
    slug: "revelation-os",
    title: "Aura Spatial Interface",
    client: "Aura Dynamics",
    year: "2026",
    category: "Product & Spatial UX",
    role: "Lead Architecture & Design",
    summary: "A next-generation spatial computing environment engineered for zero-latency creative production.",
    heroImage: "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?auto=format&fit=crop&w=1600&q=80",
    featured: true,
    metrics: [
      { label: "Performance", value: "60 FPS" },
      { label: "Latency", value: "<12ms" }
    ]
  },
  {
    id: "2",
    slug: "spyker-aerodynamics",
    title: "Kinetic Velocity Study",
    client: "Spyker Automobili",
    year: "2026",
    category: "Bespoke Automotive",
    role: "Creative Direction & Engineering",
    summary: "Sculptural aerodynamics and telemetry visualizations celebrating raw mechanical precision.",
    heroImage: "https://images.unsplash.com/photo-1542282088-72c9c27ed0cd?auto=format&fit=crop&w=1600&q=80",
    featured: true,
    metrics: [
      { label: "Downforce", value: "+38%" },
      { label: "Drag Coeff", value: "0.28 Cd" }
    ]
  },
  {
    id: "3",
    slug: "tokonoma-pavilion",
    title: "Monolithic Sound Architecture",
    client: "Tokonoma Kyoto",
    year: "2025",
    category: "Exhibition & Digital Space",
    role: "Sound & Interaction Design",
    summary: "An acoustic pavilion investigating the quiet tension between raw brutalist stone and generative audio.",
    heroImage: "https://images.unsplash.com/photo-1600585154340-be6161a56a0c?auto=format&fit=crop&w=1600&q=80",
    featured: false
  },
  {
    id: "4",
    slug: "velour-editorial",
    title: "Cinematic Rhythm Series",
    client: "Velour Pictures",
    year: "2025",
    category: "Film Direction",
    role: "Visual Production",
    summary: "Experimental 35mm film captures tracing nocturnal industrial choreography in Berlin and Tokyo.",
    heroImage: "https://images.unsplash.com/photo-1492691527719-9d1e07e534b4?auto=format&fit=crop&w=1600&q=80",
    featured: false
  }
];
"""
    (data_dir / "projects.ts").write_text(projects_ts, encoding="utf-8")

    # 7. Components directory
    components_dir = out_dir / "components"
    components_dir.mkdir(parents=True, exist_ok=True)

    # Navbar.tsx
    navbar_tsx = f"""\"use client\";

import React, {{ useState, useEffect }} from "react";
import {{ ArrowUpRight, Menu, X }} from "lucide-react";

export function Navbar() {{
  const [scrolled, setScrolled] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  useEffect(() => {{
    const handleScroll = () => setScrolled(window.scrollY > 20);
    window.addEventListener("scroll", handleScroll);
    return () => window.removeEventListener("scroll", handleScroll);
  }}, []);

  return (
    <header className="fixed top-0 left-0 right-0 z-50 flex justify-center p-4 md:p-6 transition-all duration-300">
      <nav
        className={{`w-full max-w-5xl flex items-center justify-between px-6 py-3.5 transition-all duration-300 ${{
          scrolled
            ? "rounded-full bg-surface/70 backdrop-blur-xl border border-border shadow-2xl"
            : "bg-transparent border-transparent"
        }}`}}
      >
        <a href="#" className="flex items-center gap-2 group">
          <div className="w-2.5 h-2.5 rounded-full bg-accent animate-pulse" />
          <span className="font-medium tracking-tight text-text-primary group-hover:text-accent transition-colors">
            {project_name.upper()}
          </span>
        </a>

        <div className="hidden md:flex items-center gap-8 text-sm text-text-muted">
          <a href="#work" className="hover:text-text-primary transition-colors">Work</a>
          <a href="#about" className="hover:text-text-primary transition-colors">About</a>
          <a href="#philosophy" className="hover:text-text-primary transition-colors">Philosophy</a>
          <a href="#contact" className="hover:text-text-primary transition-colors">Contact</a>
        </div>

        <div className="hidden md:flex items-center gap-4">
          <a
            href="#contact"
            className="flex items-center gap-1.5 px-4 py-1.5 rounded-full bg-surface border border-border text-xs font-mono tracking-widest text-text-primary hover:border-accent hover:text-accent transition-all duration-200"
          >
            <span>INQUIRE</span>
            <ArrowUpRight className="w-3.5 h-3.5" />
          </a>
        </div>

        <button
          onClick={{() => setMobileMenuOpen(!mobileMenuOpen)}}
          className="md:hidden text-text-muted hover:text-text-primary p-1"
        >
          {{mobileMenuOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}}
        </button>
      </nav>
    </header>
  );
}}
"""
    (components_dir / "Navbar.tsx").write_text(navbar_tsx, encoding="utf-8")

    # Hero.tsx
    hero_tsx = f"""\"use client\";

import React from \"react\";
import {{ ArrowDownRight }} from \"lucide-react\";

export function Hero() {{
  return (
    <section className=\"relative min-h-[85vh] flex flex-col justify-end px-6 md:px-12 pb-16 pt-32 max-w-7xl mx-auto\">
      <div className=\"flex flex-col gap-6 max-w-4xl\">
        <div className=\"flex items-center gap-3\">
          <span className=\"px-3 py-1 rounded-full bg-surface border border-border text-[11px] font-mono tracking-widest uppercase text-accent\">
            {arch['name']}
          </span>
          <span className=\"text-xs font-mono tracking-widest text-text-muted uppercase\">
            Edition 2026
          </span>
        </div>

        <h1 className=\"text-4xl sm:text-6xl md:text-7xl lg:text-8xl font-normal tracking-tighter leading-[0.95] text-text-primary\">
          {concept}
        </h1>

        <p className=\"text-lg md:text-xl text-text-muted max-w-2xl font-light leading-relaxed mt-2\">
          Engineered from reverse-engineered production architectures. Zero template fluff, precise typographic cadence, and deliberate negative space.
        </p>

        <div className=\"flex items-center gap-6 pt-4\">
          <a
            href=\"#work\"
            className=\"flex items-center gap-2 px-6 py-3.5 rounded-full bg-accent text-background font-medium text-sm tracking-wide hover:opacity-90 transition-opacity\"
          >
            <span>Explore Archive</span>
            <ArrowDownRight className=\"w-4 h-4\" />
          </a>
          <span className=\"text-xs font-mono tracking-widest text-text-muted uppercase\">
            Scroll to inspect
          </span>
        </div>
      </div>
    </section>
  );
}}
"""
    (components_dir / "Hero.tsx").write_text(hero_tsx, encoding="utf-8")

    # ProjectCard.tsx
    card_tsx = """\"use client\";

import React from \"react\";
import { ArrowUpRight } from \"lucide-react\";
import { Project } from \"../data/projects\";

export function ProjectCard({ project }: { project: Project }) {
  return (
    <article className=\"group relative flex flex-col bg-surface/50 border border-border rounded-xl overflow-hidden hover:border-accent/40 transition-all duration-300\">
      <div className=\"relative aspect-[16/10] overflow-hidden bg-surface-elevated\">
        <img
          src={project.heroImage}
          alt={project.title}
          className=\"w-full h-full object-cover group-hover:scale-105 transition-transform duration-700 ease-out\"
          loading=\"lazy\"
        />
        <div className=\"absolute inset-0 bg-gradient-to-t from-background/80 via-transparent to-transparent opacity-60\" />
        <div className=\"absolute top-4 right-4 flex gap-2\">
          <span className=\"px-2.5 py-1 rounded-full bg-background/80 backdrop-blur-md border border-border text-[10px] font-mono tracking-wider text-text-muted uppercase\">
            {project.year}
          </span>
        </div>
      </div>

      <div className=\"p-6 md:p-8 flex flex-col justify-between flex-grow\">
        <div>
          <div className=\"flex items-center justify-between gap-4 mb-2\">
            <span className=\"text-xs font-mono tracking-widest uppercase text-accent\">
              {project.category}
            </span>
            <ArrowUpRight className=\"w-4 h-4 text-text-muted group-hover:text-accent group-hover:translate-x-0.5 group-hover:-translate-y-0.5 transition-all duration-200\" />
          </div>

          <h3 className=\"text-2xl font-normal tracking-tight text-text-primary group-hover:text-accent transition-colors\">
            {project.title}
          </h3>

          <p className=\"text-sm text-text-muted leading-relaxed mt-2 line-clamp-2\">
            {project.summary}
          </p>
        </div>

        <div className=\"pt-6 mt-6 border-t border-border/60 flex items-center justify-between text-xs font-mono text-text-muted\">
          <span>{project.client}</span>
          <span>{project.role}</span>
        </div>
      </div>
    </article>
  );
}
"""
    (components_dir / "ProjectCard.tsx").write_text(card_tsx, encoding="utf-8")

    # ProjectGrid.tsx
    grid_tsx = """\"use client\";

import React from \"react\";
import { PROJECTS } from \"../data/projects\";
import { ProjectCard } from \"./ProjectCard\";

export function ProjectGrid() {
  return (
    <section id=\"work\" className=\"px-6 md:px-12 py-24 max-w-7xl mx-auto\">
      <div className=\"flex flex-col md:flex-row md:items-end justify-between mb-16 pb-6 border-b border-border\">
        <div>
          <span className=\"text-xs font-mono tracking-widest text-accent uppercase block mb-2\">
            Selected Works
          </span>
          <h2 className=\"text-3xl md:text-5xl font-normal tracking-tight text-text-primary\">
            Curated Showcase
          </h2>
        </div>
        <p className=\"text-sm text-text-muted max-w-md mt-4 md:mt-0 font-light\">
          Reverse-engineered design tokens applied across multi-format digital experiences.
        </p>
      </div>

      <div className=\"grid grid-cols-1 md:grid-cols-2 gap-8\">
        {PROJECTS.map((project) => (
          <ProjectCard key={project.id} project={project} />
        ))}
      </div>
    </section>
  );
}
"""
    (components_dir / "ProjectGrid.tsx").write_text(grid_tsx, encoding="utf-8")

    # Footer.tsx
    footer_tsx = f"""\"use client\";

import React from \"react\";

export function Footer() {{
  return (
    <footer id=\"contact\" className=\"px-6 md:px-12 py-20 border-t border-border bg-surface/30\">
      <div className=\"max-w-7xl mx-auto flex flex-col justify-between min-h-[40vh]\">
        <div className=\"flex flex-col md:flex-row justify-between gap-12\">
          <div>
            <span className=\"text-xs font-mono tracking-widest text-accent uppercase block mb-3\">
              Start a Conversation
            </span>
            <h2 className=\"text-3xl md:text-5xl font-light tracking-tight text-text-primary max-w-lg\">
              Let's create something non-standard.
            </h2>
          </div>

          <div className=\"flex flex-col sm:flex-row gap-12 text-sm text-text-muted font-light\">
            <div>
              <span className=\"text-xs font-mono text-text-primary block mb-3 uppercase tracking-wider\">Index</span>
              <ul className=\"space-y-2\">
                <li><a href=\"#work\" className=\"hover:text-text-primary transition-colors\">Work</a></li>
                <li><a href=\"#about\" className=\"hover:text-text-primary transition-colors\">About</a></li>
                <li><a href=\"#philosophy\" className=\"hover:text-text-primary transition-colors\">Philosophy</a></li>
              </ul>
            </div>
            <div>
              <span className=\"text-xs font-mono text-text-primary block mb-3 uppercase tracking-wider\">Network</span>
              <ul className=\"space-y-2\">
                <li><a href=\"https://github.com\" target=\"_blank\" className=\"hover:text-text-primary transition-colors\">GitHub</a></li>
                <li><a href=\"https://twitter.com\" target=\"_blank\" className=\"hover:text-text-primary transition-colors\">X / Twitter</a></li>
                <li><a href=\"https://linkedin.com\" target=\"_blank\" className=\"hover:text-text-primary transition-colors\">LinkedIn</a></li>
              </ul>
            </div>
          </div>
        </div>

        <div className=\"pt-16 mt-16 border-t border-border/60 flex flex-col sm:flex-row items-center justify-between text-xs font-mono text-text-muted gap-4\">
          <span>© {{new Date().getFullYear()}} {project_name.upper()} • High-Craft Generation</span>
          <span>Reverse-Engineered DNA • Anti-AI Slop</span>
        </div>
      </div>
    </footer>
  );
}}
"""
    (components_dir / "Footer.tsx").write_text(footer_tsx, encoding="utf-8")

    # app/page.tsx
    page_tsx = """import { Navbar } from "@/components/Navbar";
import { Hero } from "@/components/Hero";
import { ProjectGrid } from "@/components/ProjectGrid";
import { Footer } from "@/components/Footer";

export default function Home() {
  return (
    <main className="min-h-screen bg-background">
      <Navbar />
      <Hero />
      <ProjectGrid />
      <Footer />
    </main>
  );
}
"""
    (app_dir / "page.tsx").write_text(page_tsx, encoding="utf-8")

    # .gitignore
    (out_dir / ".gitignore").write_text("""node_modules/
.next/
out/
build/
.DS_Store
*.pem
npm-debug.log*
yarn-debug.log*
yarn-error.log*
.env*.local
.vercel
""", encoding="utf-8")

    # Initialize Git repository
    subprocess.run(["git", "init"], cwd=str(out_dir), capture_output=True)

    console.print(f"[bold green]✓ Scaffolding complete for {project_name}![/bold green]")
    console.print(f"  • Tech Stack: Next.js 15 (App Router), Tailwind CSS, Framer Motion, Lucide Icons")
    console.print(f"  • Design Archetype: {arch['name']}")
    console.print(f"  • Target Repo: {out_dir}")

    return out_dir
