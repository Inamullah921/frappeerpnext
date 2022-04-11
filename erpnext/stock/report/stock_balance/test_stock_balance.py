from typing import Any, Dict

import frappe
from frappe import _dict
from frappe.tests.utils import FrappeTestCase
from frappe.utils import today

from erpnext.stock.doctype.item.test_item import make_item
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from erpnext.stock.report.stock_balance.stock_balance import execute


def stock_balance(filters):
	return list(map(_dict, execute(filters)[1]))


class TestStockBalance(FrappeTestCase):
	# ----------- utils

	def setUp(self):
		self.item = make_item()
		self.filters = _dict(
			{
				"company": "_Test Company",
				"item_code": self.item.name,
				"from_date": "2020-01-01",
				"to_date": str(today()),
			}
		)

	def tearDown(self):
		frappe.db.rollback()

	def assertPartialDictionary(self, expected: Dict[str, Any], actual: Dict[str, Any]):
		for k, v in expected.items():
			self.assertEqual(v, actual[k], msg=f"{expected=}\n{actual=}")

	def generate_stock_ledger(self, item_code: str, movements):

		for movement in map(_dict, movements):
			make_stock_entry(
				item_code=item_code,
				**movement,
				to_warehouse=movement.to_warehouse or "_Test Warehouse - _TC",
			)

	def assertInvariants(self, rows):
		last_balance = frappe.db.sql(
			"""
			WITH last_balances AS (
				SELECT item_code, warehouse,
					stock_value, qty_after_transaction,
					ROW_NUMBER() OVER (PARTITION BY item_code, warehouse
						ORDER BY timestamp(posting_date, posting_time) desc, creation desc)
						AS rn
					FROM `tabStock Ledger Entry`
					where is_cancelled=0
				)
				SELECT * FROM last_balances WHERE rn = 1""",
			as_dict=True,
		)

		item_wh_stock = _dict()

		for line in last_balance:
			item_wh_stock.setdefault((line.item_code, line.warehouse), line)

		for row in rows:
			msg = f"Invariants not met for {rows=}"
			# qty invariant
			self.assertAlmostEqual(row.bal_qty, row.opening_qty + row.in_qty - row.out_qty, msg)

			# value invariant
			self.assertAlmostEqual(row.bal_val, row.opening_val + row.in_val - row.out_val, msg)

			# valuation rate
			self.assertAlmostEqual(row.val_rate, row.bal_val / row.bal_qty, 3, msg)

			# check against SLE
			last_sle = item_wh_stock[(row.item_code, row.warehouse)]
			self.assertAlmostEqual(row.bal_qty, last_sle.qty_after_transaction, 3)
			self.assertAlmostEqual(row.bal_val, last_sle.stock_value, 3)

	# ----------- tests

	def test_basic_stock_balance(self):
		"""Check very basic functionality and item info"""
		rows = stock_balance(self.filters)
		self.assertEqual(rows, [])

		self.generate_stock_ledger(self.item.name, [_dict(qty=5, rate=10)])

		# check item info
		rows = stock_balance(self.filters)
		self.assertPartialDictionary(
			{
				"item_code": self.item.name,
				"item_name": self.item.item_name,
				"item_group": self.item.item_group,
				"stock_uom": self.item.stock_uom,
				"in_qty": 5,
				"in_val": 50,
				"val_rate": 10,
			},
			rows[0],
		)
		self.assertInvariants(rows)

	def test_opening_balance(self):
		self.generate_stock_ledger(
			self.item.name,
			[
				_dict(qty=1, rate=1, posting_date="2021-01-01"),
				_dict(qty=2, rate=2, posting_date="2021-01-02"),
				_dict(qty=3, rate=3, posting_date="2021-01-03"),
			],
		)
		rows = stock_balance(self.filters)
		self.assertInvariants(rows)

		rows = stock_balance(self.filters.update({"from_date": "2021-01-02"}))
		self.assertInvariants(rows)
		self.assertPartialDictionary({"opening_qty": 1, "in_qty": 5}, rows[0])

		rows = stock_balance(self.filters.update({"from_date": "2022-01-01"}))
		self.assertInvariants(rows)
		self.assertPartialDictionary({"opening_qty": 6, "in_qty": 0}, rows[0])

	def test_uom_converted_info(self):

		self.item.append("uoms", {"conversion_factor": 5, "uom": "Box"})
		self.item.save()

		self.generate_stock_ledger(self.item.name, [_dict(qty=5, rate=10)])

		rows = stock_balance(self.filters.update({"include_uom": "Box"}))
		self.assertEqual(rows[0].bal_qty_alt, 1)
