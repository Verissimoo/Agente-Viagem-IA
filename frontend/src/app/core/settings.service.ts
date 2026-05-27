import { Injectable, signal } from '@angular/core';

/**
 * Configurações de fontes/comportamento da busca, persistidas em
 * localStorage. Espelha o painel do legado:
 *
 *   • Provedor de milhas (BuscaMilhas | Economilhas | Ambos)
 *   • Dados Estáticos (Mock)
 *   • Companhias BuscaMilhas — nacionais e internacionais
 *   • Programas Economilhas (Smiles, LATAM Pass, TudoAzul, Azul Pelo Mundo,
 *     COPA, Iberia Plus, British Avios)
 *   • Dinheiro (Kayak)
 *   • MCP Award + Qatar (internacional via MCP)
 *   • Skiplagged (hidden city + split cash)
 */
const STORAGE_KEY = 'agente-viagem.settings.v2';

const ALL_COMPANHIAS = [
  'LATAM',
  'GOL',
  'AZUL',
  'TAP',
  'IBERIA',
  'AMERICAN',
  'COPA',
  'INTERLINE',
] as const;
export type Companhia = (typeof ALL_COMPANHIAS)[number];

const ALL_PROGRAMS = [
  'SMILES',
  'LATAM_PASS',
  'AZUL_FIDELIDADE',
  'AZUL_INTERLINE',
  'COPA',
  'IBERIA',
  'BRITISH',
] as const;
export type EconomilhasProgram = (typeof ALL_PROGRAMS)[number];

export const PROGRAM_LABEL: Record<EconomilhasProgram, string> = {
  SMILES: 'Smiles (GOL)',
  LATAM_PASS: 'LATAM Pass',
  AZUL_FIDELIDADE: 'Azul Fidelidade',
  AZUL_INTERLINE: 'Azul Pelo Mundo (Interline)',
  COPA: 'Copa ConnectMiles',
  IBERIA: 'Iberia Plus',
  BRITISH: 'British Airways Avios',
};

export type ProvedorMilhas = 'buscamilhas' | 'economilhas' | 'both';

interface PersistedSettings {
  provedorMilhas: ProvedorMilhas;
  companhiasBuscamilhas: Companhia[];
  programasEconomilhas: EconomilhasProgram[];
  includeKayak: boolean;
  includeMcp: boolean;
  includeSkiplagged: boolean;
  useFixtures: boolean;
  forceRefreshDefault: boolean;
}

const DEFAULTS: PersistedSettings = {
  provedorMilhas: 'both',
  companhiasBuscamilhas: ['LATAM', 'GOL', 'AZUL'],
  programasEconomilhas: ['SMILES', 'LATAM_PASS', 'AZUL_FIDELIDADE'],
  includeKayak: true,
  includeMcp: true,
  includeSkiplagged: true,
  useFixtures: false,
  forceRefreshDefault: false,
};

@Injectable({ providedIn: 'root' })
export class SettingsService {
  readonly ALL_COMPANHIAS = ALL_COMPANHIAS;
  readonly ALL_PROGRAMS = ALL_PROGRAMS;
  readonly PROGRAM_LABEL = PROGRAM_LABEL;

  provedorMilhas = signal<ProvedorMilhas>(DEFAULTS.provedorMilhas);
  companhiasBuscamilhas = signal<Companhia[]>(DEFAULTS.companhiasBuscamilhas);
  programasEconomilhas = signal<EconomilhasProgram[]>(DEFAULTS.programasEconomilhas);
  includeKayak = signal(DEFAULTS.includeKayak);
  includeMcp = signal(DEFAULTS.includeMcp);
  includeSkiplagged = signal(DEFAULTS.includeSkiplagged);
  useFixtures = signal(DEFAULTS.useFixtures);
  forceRefreshDefault = signal(DEFAULTS.forceRefreshDefault);

  constructor() {
    this.restore();
  }

  setProvedorMilhas(v: ProvedorMilhas): void {
    this.provedorMilhas.set(v);
    this.persist();
  }

  toggleCompanhia(c: Companhia, on: boolean): void {
    const current = new Set(this.companhiasBuscamilhas());
    if (on) current.add(c);
    else current.delete(c);
    this.companhiasBuscamilhas.set([...current]);
    this.persist();
  }

  toggleProgram(p: EconomilhasProgram, on: boolean): void {
    const current = new Set(this.programasEconomilhas());
    if (on) current.add(p);
    else current.delete(p);
    this.programasEconomilhas.set([...current]);
    this.persist();
  }

  isCompanhiaOn(c: Companhia): boolean {
    return this.companhiasBuscamilhas().includes(c);
  }

  isProgramOn(p: EconomilhasProgram): boolean {
    return this.programasEconomilhas().includes(p);
  }

  setIncludeKayak(v: boolean): void { this.includeKayak.set(v); this.persist(); }
  setIncludeMcp(v: boolean): void { this.includeMcp.set(v); this.persist(); }
  setIncludeSkiplagged(v: boolean): void { this.includeSkiplagged.set(v); this.persist(); }
  setUseFixtures(v: boolean): void { this.useFixtures.set(v); this.persist(); }
  setForceRefreshDefault(v: boolean): void { this.forceRefreshDefault.set(v); this.persist(); }

  /** Lista de companhias/fontes a enviar no payload do /search.
   * O orchestrator do backend usa essa lista para decidir quais adapters disparar. */
  companhiasPayload(): string[] {
    const base: string[] = [];
    const prov = this.provedorMilhas();
    if (prov === 'buscamilhas' || prov === 'both') {
      base.push(...this.companhiasBuscamilhas());
    }
    if (prov === 'economilhas' || prov === 'both') {
      base.push('ECONOMILHAS');
    }
    if (this.includeKayak()) base.push('KAYAK');
    if (this.includeMcp()) base.push('MCP_AWARD', 'QATAR');
    if (this.includeSkiplagged()) base.push('SKIPLAGGED');
    return base;
  }

  /** Companhias visíveis para a UI dependendo do provedor selecionado.
   * Permite renderizar o painel diferente conforme escolha. */
  showsBuscamilhas(): boolean {
    const p = this.provedorMilhas();
    return p === 'buscamilhas' || p === 'both';
  }
  showsEconomilhas(): boolean {
    const p = this.provedorMilhas();
    return p === 'economilhas' || p === 'both';
  }

  reset(): void {
    this.provedorMilhas.set(DEFAULTS.provedorMilhas);
    this.companhiasBuscamilhas.set([...DEFAULTS.companhiasBuscamilhas]);
    this.programasEconomilhas.set([...DEFAULTS.programasEconomilhas]);
    this.includeKayak.set(DEFAULTS.includeKayak);
    this.includeMcp.set(DEFAULTS.includeMcp);
    this.includeSkiplagged.set(DEFAULTS.includeSkiplagged);
    this.useFixtures.set(DEFAULTS.useFixtures);
    this.forceRefreshDefault.set(DEFAULTS.forceRefreshDefault);
    this.persist();
  }

  private persist(): void {
    try {
      const snapshot: PersistedSettings = {
        provedorMilhas: this.provedorMilhas(),
        companhiasBuscamilhas: this.companhiasBuscamilhas(),
        programasEconomilhas: this.programasEconomilhas(),
        includeKayak: this.includeKayak(),
        includeMcp: this.includeMcp(),
        includeSkiplagged: this.includeSkiplagged(),
        useFixtures: this.useFixtures(),
        forceRefreshDefault: this.forceRefreshDefault(),
      };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot));
    } catch {
      // localStorage indisponível — silenciar.
    }
  }

  private restore(): void {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw) as Partial<PersistedSettings>;
      if (parsed.provedorMilhas === 'buscamilhas' || parsed.provedorMilhas === 'economilhas' || parsed.provedorMilhas === 'both') {
        this.provedorMilhas.set(parsed.provedorMilhas);
      }
      if (Array.isArray(parsed.companhiasBuscamilhas)) {
        const valid = parsed.companhiasBuscamilhas.filter((c): c is Companhia =>
          (ALL_COMPANHIAS as readonly string[]).includes(c),
        );
        this.companhiasBuscamilhas.set(valid);
      }
      if (Array.isArray(parsed.programasEconomilhas)) {
        const valid = parsed.programasEconomilhas.filter((p): p is EconomilhasProgram =>
          (ALL_PROGRAMS as readonly string[]).includes(p),
        );
        this.programasEconomilhas.set(valid);
      }
      if (typeof parsed.includeKayak === 'boolean') this.includeKayak.set(parsed.includeKayak);
      if (typeof parsed.includeMcp === 'boolean') this.includeMcp.set(parsed.includeMcp);
      if (typeof parsed.includeSkiplagged === 'boolean') this.includeSkiplagged.set(parsed.includeSkiplagged);
      if (typeof parsed.useFixtures === 'boolean') this.useFixtures.set(parsed.useFixtures);
      if (typeof parsed.forceRefreshDefault === 'boolean') this.forceRefreshDefault.set(parsed.forceRefreshDefault);
    } catch {
      // settings corrompido — ignora e usa defaults
    }
  }
}
