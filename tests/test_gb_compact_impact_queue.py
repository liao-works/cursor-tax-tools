"""GB official full compact 103 candidate affected-code queue exact-code safety."""
import unittest
from src.target_duty.gb_compact_impact_queue import impact


def snapshot(version,codes,day="2026-10-14"):
    return {"version":version,"as_of":day,"sha256":"a"*64,"codes":codes}


def row(state="candidate_present_requires_applicability_review",values=()):
    return {"status":state,"active":list(values),"all":list(values)}


class GBCompactImpactTests(unittest.TestCase):
    def test_all_changed_codes_not_sample_subset(self):
        before=snapshot("v4.0.1627",{"0101210000":row(values=['103:5%']),
            "0101299000":row(values=['103:10%']),"0301110000":row(values=[])})
        after=snapshot("v4.0.1628",{"0101210000":row(values=['103:5%']),
            "0101299000":row(values=['103:12%']),"0401110000":row(values=[])},
            "2026-10-15")
        changes=impact(before,after)
        self.assertEqual(len(changes),3)
        self.assertEqual([x["code"] for x in changes],
                         ["0101299000","0301110000","0401110000"])
        self.assertTrue(all(x["payable_rate"]=="" for x in changes))
        self.assertTrue(all(x["production_eligible"]=="false" for x in changes))

    def test_context_status_changes_without_rate_are_detected(self):
        a=snapshot("v4.0.1627",{"0101210000":row()})
        b=snapshot("v4.0.1628",{"0101210000":row(
            "no_active_103_1011_unresolved")})
        self.assertEqual(len(impact(a,b)),1)

    def test_older_pin_or_effective_day_rejected(self):
        a=snapshot("v4.0.1628",{"0101210000":row()},"2026-10-15")
        b=snapshot("v4.0.1627",{"0101210000":row()},"2026-10-14")
        with self.assertRaisesRegex(ValueError,"Backward"):
            impact(a,b)


if __name__=="__main__":
    unittest.main()
