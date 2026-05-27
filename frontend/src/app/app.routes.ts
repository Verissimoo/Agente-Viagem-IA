import { Routes } from '@angular/router';

import { SearchPageComponent } from './features/search-page/search-page';

export const routes: Routes = [
  { path: '', component: SearchPageComponent },
  { path: '**', redirectTo: '' },
];
