import { expect, test, vi } from 'vitest';
import { buildCashflowDays, currencyTotals, filterTransactions, loadMonthTransactions } from '../app/static/finance-domain.js';
test('totals preserve cents beyond floating point precision and keep currencies separate', () => {
  expect(currencyTotals([
    {currency:'EUR',amount_exact:'9999999999999999.99'},
    {currency:'EUR',amount_exact:'0.01'},
    {currency:'USD',amount_exact:'-12.34'},
    {currency:null,amount_exact:'3.25'},
  ])).toEqual([
    {currency:'EUR',amount:'10000000000000000.00'},
    {currency:'USD',amount:'-12.34'},
    {currency:'XXX',amount:'3.25'},
  ]);
});
test('combined filters include split allocations, with distinct uncategorized results', () => {
  const rows = [
    {id:1,description:'Grocery SHOP',counterparty_name:'Transport Company',account_id:2,direction:'DBIT',category_id:null,splits:[{category_id:5}]},
    {id:2,description:'Shop',account_id:3,direction:'CRDT',category_id:5},
    {id:3,description:'Rent',account_id:2,direction:'DBIT',category_id:null},
  ];
  expect(filterTransactions(rows,{search:'shop',account:'2',category:'5',direction:'DBIT'}).map(row => row.id)).toEqual([1]);
  expect(filterTransactions(rows,{search:'transport'}).map(row => row.id)).toEqual([1]);
  expect(filterTransactions(rows,{category:'none'}).map(row => row.id)).toEqual([3]);
});
test('cashflow ranges include only the requested trailing calendar days', () => {
  const rows = [
    {date:'2026-11-14T08:00:00Z',direction:'DBIT',amount_exact:'12.50'},
    {date:'2026-11-12T08:00:00Z',direction:'CRDT',amount_exact:'20.00'},
    {date:'2026-11-10T08:00:00Z',direction:'DBIT',amount_exact:'99.00'},
  ];
  expect(buildCashflowDays(rows, 3, new Date('2026-11-14T12:00:00Z'))).toEqual([
    {date:'2026-11-12',income:20,expense:0},
    {date:'2026-11-13',income:0,expense:0},
    {date:'2026-11-14',income:0,expense:12.5},
  ]);
});
test('loads every page before totals and sorts by banking date, independently of insertion order', async () => {
  const first = Array.from({length:1000},(_,i) => ({id:1002-i,date:'2026-11-01',currency:'EUR',amount_exact:'0.01'}));
  const fetcher = vi.fn().mockResolvedValueOnce(first).mockResolvedValueOnce([{id:2,date:'2026-11-02',currency:'EUR',amount_exact:'0.25'}]);
  const result = await loadMonthTransactions(fetcher,'2026-11');
  expect(fetcher.mock.calls[1][0]).toContain('before_id=3');
  expect(result).toHaveLength(1001);
  expect(result[0].id).toBe(2);
  expect(currencyTotals(result)).toEqual([{currency:'EUR',amount:'10.25'}]);
});
test('a failed page never returns a partial month as complete', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(Array.from({length:1000},(_,i) => ({id:1001-i}))).mockRejectedValueOnce(new Error('offline'));
  await expect(loadMonthTransactions(fetcher,'2026-11')).rejects.toThrow('offline');
});
