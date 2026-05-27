import { CommonModule } from '@angular/common';
import { Component, EventEmitter, OnInit, Output, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { ApiService } from '../../core/api.service';
import {
  Companhia,
  EconomilhasProgram,
  ProvedorMilhas,
  SettingsService,
} from '../../core/settings.service';
import { RateTier, RatesResponseDTO } from '../../models/flight';
import { programLabel } from '../helpers';

type TabKey = 'rates' | 'sources' | 'advanced';

interface EditableTier {
  max_miles: number | null;
  rate: number;
}

interface EditableProgram {
  key: string;
  label: string;
  tiers: EditableTier[];
}

@Component({
  selector: 'app-rates-editor',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './rates-editor.html',
  styleUrl: './rates-editor.scss',
})
export class RatesEditorComponent implements OnInit {
  @Output() close = new EventEmitter<void>();
  @Output() saved = new EventEmitter<RatesResponseDTO>();

  private api = inject(ApiService);
  readonly settings = inject(SettingsService);

  activeTab = signal<TabKey>('rates');

  loading = signal(false);
  saving = signal(false);
  error = signal<string | null>(null);
  programs = signal<EditableProgram[]>([]);
  fallbackRate = signal(0.05);
  estimationProgram = signal('GOL');

  setTab(t: TabKey): void { this.activeTab.set(t); }

  /** Helpers para ngModel em checkbox vinculado ao SettingsService. */
  isCompanhiaOn(c: Companhia): boolean {
    return this.settings.isCompanhiaOn(c);
  }
  toggleCompanhia(c: Companhia, ev: Event): void {
    const on = (ev.target as HTMLInputElement).checked;
    this.settings.toggleCompanhia(c, on);
  }

  isProgramOn(p: EconomilhasProgram): boolean {
    return this.settings.isProgramOn(p);
  }
  toggleProgram(p: EconomilhasProgram, ev: Event): void {
    const on = (ev.target as HTMLInputElement).checked;
    this.settings.toggleProgram(p, on);
  }

  setProvedor(v: ProvedorMilhas): void {
    this.settings.setProvedorMilhas(v);
  }

  // Partições da grade de companhias — espelha o legado:
  // Nacionais (LATAM/GOL/AZUL) ficam separadas das Internacionais.
  readonly COMPANHIAS_NACIONAIS: Companhia[] = ['LATAM', 'GOL', 'AZUL'];
  readonly COMPANHIAS_INTERNACIONAIS: Companhia[] = ['TAP', 'AMERICAN', 'INTERLINE', 'COPA', 'IBERIA'];

  // Two-way bindings for ngModel
  get fallbackRateValue(): number { return this.fallbackRate(); }
  set fallbackRateValue(v: number) { this.fallbackRate.set(Number(v) || 0); }
  get estimationProgramValue(): string { return this.estimationProgram(); }
  set estimationProgramValue(v: string) { this.estimationProgram.set(v); }

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.api.getRates().subscribe({
      next: (resp) => {
        this.programs.set(this.toEditable(resp.programs));
        this.fallbackRate.set(resp.international_fallback_rate);
        this.estimationProgram.set(resp.skiplagged_estimation_program);
        this.loading.set(false);
      },
      error: (e) => {
        this.error.set(e?.message ?? 'Falha ao carregar rates');
        this.loading.set(false);
      },
    });
  }

  save(): void {
    this.saving.set(true);
    this.error.set(null);
    const payload: RatesResponseDTO = {
      programs: this.toPayload(this.programs()),
      international_fallback_rate: this.fallbackRate(),
      skiplagged_estimation_program: this.estimationProgram(),
    };
    this.api.updateRates(payload).subscribe({
      next: (resp) => {
        this.saving.set(false);
        this.saved.emit(resp);
        this.close.emit();
      },
      error: (e) => {
        const detail = (e?.error as { detail?: string })?.detail;
        this.error.set(detail ?? e?.message ?? 'Falha ao salvar rates');
        this.saving.set(false);
      },
    });
  }

  addTier(prog: EditableProgram): void {
    const last = prog.tiers[prog.tiers.length - 1];
    const newMax = last.max_miles ? last.max_miles + 50000 : 50000;
    last.max_miles = newMax;
    prog.tiers.push({ max_miles: null, rate: last.rate });
    this.programs.set([...this.programs()]);
  }

  removeTier(prog: EditableProgram, index: number): void {
    if (prog.tiers.length <= 1) return;
    prog.tiers.splice(index, 1);
    // Last tier must always have max_miles = null
    prog.tiers[prog.tiers.length - 1].max_miles = null;
    this.programs.set([...this.programs()]);
  }

  programLabelFor(key: string): string {
    return programLabel(key);
  }

  private toEditable(programs: Record<string, RateTier[]>): EditableProgram[] {
    return Object.entries(programs).map(([key, tiers]) => ({
      key,
      label: programLabel(key),
      tiers: tiers.map((t) => ({ max_miles: t.max_miles, rate: t.rate })),
    }));
  }

  private toPayload(progs: EditableProgram[]): Record<string, RateTier[]> {
    const out: Record<string, RateTier[]> = {};
    for (const p of progs) {
      out[p.key] = p.tiers.map((t) => ({ max_miles: t.max_miles, rate: t.rate }));
    }
    return out;
  }
}
