"""Data from accepted snapshots must never invent ambiguous or stale rates."""
import unittest
from datetime import date
from src.target_duty.desktop_release_bundle import simple_rate, xi_rate

class DesktopReleaseBundleTests(unittest.TestCase):
    def test_gb_unique_simple_rate(self):
        self.assertEqual(simple_rate([("6%","","","")]),"6%")
        self.assertEqual(simple_rate([("0.00 %","","","")]),"0.00 %")
        self.assertEqual(simple_rate([]),"")
        self.assertEqual(simple_rate([("6%","","",""),("12%","","","")]),"")
        self.assertEqual(simple_rate([("6%","","certificate","")]),"")
        self.assertEqual(simple_rate([("6%","1234","","")]),"")
        self.assertEqual(simple_rate([("6%","","","GB")]),"")
        self.assertEqual(simple_rate([("£12/100kg","","","")]),"")

    def test_xi_stale_conditional_specific_and_compound_never_auto_populate(self):
        day=date(2026,10,16)
        def row(rate="5%",typ="103",geo="1011",start="2026-01-01",
                end=None,rels=None):
            import json
            return (typ,geo,rate,start,end,json.dumps({"relationships":rels or {}}))
        self.assertEqual(xi_rate([row()],day),"5%")
        self.assertEqual(xi_rate([row(typ="105")],day),"5%")
        self.assertEqual(xi_rate([row(start="2027-01-01")],day),"")
        self.assertEqual(xi_rate([row(end="2026-10-15")],day),"")
        self.assertEqual(xi_rate([row(geo="GB")],day),"")
        self.assertEqual(xi_rate([row(rate="10% + EUR 8 / 100 kg")],day),"")
        self.assertEqual(xi_rate([row(rels={
            "measure_conditions":{"data":[{"id":"1","type":"measure_condition"}]}})],day),"")
        self.assertEqual(xi_rate([row(),row(rate="8%",typ="105")],day),"")

    def test_invalid_source_dates_cannot_be_published(self):
        import json
        self.assertEqual(xi_rate([("103","1011","6%",None,None,
                                   json.dumps({"relationships":{}}))],
                                 date(2026,10,16)),"")


if __name__=="__main__":
    unittest.main()
