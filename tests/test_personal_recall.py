import json
import unittest
from gamma.coordination.recall import rank_records

def record(id, topic, text, **values):
    return dict(id=id,topic=topic,text=text,kind='fact',subject_id='owner',scope='owner_private',
        source_scope='owner_private',source_id='source-'+id,source_kind='user_report',certainty='reported',
        confidence=.8,status='active',updated_at=100,**values)

class Recall(unittest.TestCase):
    def test_matched_retrieval_quality_and_unknown_query(self):
        examples=[('favorite_drink','Prefers green tea','What drink do I prefer?'),
          ('meeting_city','Workshop meeting is in Aspen','Where is the meeting?'),
          ('garden_gate_code','Garden gate uses 3187','What was the garden code?'),
          ('book_author','Bought a novel by Le Guin','Who wrote that novel?'),
          ('repair_decision','Decided to repair the bicycle first','What did we decide about the bicycle?'),
          ('appointment_day','Dentist appointment is Tuesday','Which day is the dentist?'),
          ('friend_hobby','Mira enjoys pottery','What hobby did Mira mention?'),
          ('meal_choice','Chose lentil soup for dinner','What was the dinner choice?'),
          ('project_name','The lighthouse project is called Beacon','What is the lighthouse project called?'),
          ('music_volume','Likes music volume low during study','How loud should music be during study?'),
          ('travel_ticket','Train ticket is booked for Friday','When is the train booked?'),
          ('gift_idea','A watercolor set would be a gift for Mira','Which gift idea did we have?'),
          ('work_deadline','The report deadline is Thursday','When is the report deadline?'),
          ('next_step','Next step is checking the roof leak','What is the next roof step?'),
          ('camera_location','Camera is in the blue drawer','Where is the camera?'),
          ('sleep_request','Asked for quiet until waking','What did I ask about waking?')]
        records=[record(str(i),topic,text) for i,(topic,text,q) in enumerate(examples)]
        hits=sum(rank_records(records,q,audience='owner_private',limit=3,max_chars=6000)[0]['id']==str(i)
                 for i,(_,_,q) in enumerate(examples))
        self.assertGreaterEqual(hits/len(examples),.87)
        self.assertEqual(rank_records(records,'unrelated quantum volcano',audience='owner_private'),[])

    def test_visibility_precedes_reading_content_and_defaults_fail_closed(self):
        class Hidden(dict):
            def get(self,key,*args):
                if key in {'text','topic'}:raise AssertionError('read hidden content')
                return super().get(key,*args)
        private=Hidden(record('secret','address','PRIVATE CANARY'))
        self.assertEqual(rank_records([private],'address'),[])
        self.assertEqual(rank_records([private],'address',audience='shared'),[])
        mixed=record('mixed','address','PRIVATE CANARY');mixed.update(scope='public')
        self.assertEqual(rank_records([mixed],'address',audience='shared'),[])
        public=record('public','tea','Tea is a beverage');public.update(scope='public',source_scope='public',extra_secret='CANARY')
        result=rank_records([public],'tea',audience='shared')
        self.assertEqual(result[0]['id'],'public')
        self.assertNotIn('extra_secret',result[0])

    def test_superseded_forgotten_and_pending_are_not_current_facts(self):
        rows=[record('new','drink','Likes tea'),record('old','drink','Likes coffee'),record('forgot','drink','Forgotten')]
        rows[1]['status']='superseded';rows[2]['status']='forgotten'
        rows.append(record('hyp','drink','Maybe likes cocoa'));rows[-1]['status']='pending'
        result=rank_records(rows,'drink',audience='owner_private')
        self.assertEqual([r['id'] for r in result],['new'])
        self.assertEqual(result[0]['certainty'],'reported')

    def test_subject_budget_no_mutation_and_no_arbitrary_metadata(self):
        rows=[record(str(i),'meeting','meeting '+('x'*2000)) for i in range(20)]
        rows[0]['subject_id']='person-other'
        original=json.dumps(rows,sort_keys=True)
        result=rank_records(rows,'meeting',audience='owner_private',subject_id='owner',limit=20,max_chars=1400)
        self.assertLessEqual(len(json.dumps(result,ensure_ascii=False)),1400)
        self.assertTrue(result)
        self.assertLessEqual(len(result),8)
        self.assertTrue(all(r['subject_id']=='owner' for r in result))
        self.assertEqual(json.dumps(rows,sort_keys=True),original)
        self.assertEqual(rank_records(rows,'meeting',audience='owner_private',limit=0),[])

if __name__=='__main__':unittest.main()
