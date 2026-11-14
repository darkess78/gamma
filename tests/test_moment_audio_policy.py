import copy, hashlib, unittest
from gamma.coordination.moment_audio_policy import checked_moment_text

def records():
    text='That captured scene looked peaceful.'
    review={'channel':'shared_activity','status':'completed','response':{'response_action':'reply','delivery_mode':'text_only','display_text':text,'speech_text':None,'audio_path':None}}
    moment={'status':'generated','result':{'text':text,'text_sha256':hashlib.sha256(text.encode()).hexdigest()}}
    return review,moment

class Policy(unittest.TestCase):
    def test_valid(self):
        r,m=records();self.assertEqual(checked_moment_text(r,m),m['result']['text'])
        r['response']['response_action']='acknowledge';self.assertEqual(checked_moment_text(r,m),m['result']['text'])
        for key in ('speech_text','audio_path'): r['response'].pop(key)
        self.assertEqual(checked_moment_text(r,m),m['result']['text'])
    def test_reject_review_and_response_mutations(self):
        for path,value in [(('channel',),'private_stream'),(('status',),'pending'),(('response',),None),(('response','response_action'),'stay_silent'),(('response','delivery_mode'),'speech'),(('response','speech_text'),'different'),(('response','audio_path'),'/tmp/x'),(('response','display_text'),'forged')]:
            r,m=records(); d=r
            for k in path[:-1]:d=d[k]
            d[path[-1]]=value
            with self.subTest(path=path,value=value),self.assertRaises(ValueError):checked_moment_text(r,m)
    def test_reject_moment_mutations(self):
        for key,value in [('status','cancelled'),('redacted',True),('own_text_redacted',True),('result',None)]:
            r,m=records();m[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):checked_moment_text(r,m)
        for key,value in [('text','forged'),('text',None),('text_sha256','0'*64)]:
            r,m=records();m['result'][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):checked_moment_text(r,m)
    def test_empty_and_limits_and_exact_bytes(self):
        for text in ['', '   ', 'a'*1601]:
            r,m=records();r['response']['display_text']=m['result']['text']=text;m['result']['text_sha256']=hashlib.sha256(text.encode()).hexdigest()
            with self.subTest(size=len(text)),self.assertRaises(ValueError):checked_moment_text(r,m)
        r,m=records();r['response']['display_text']=' '+m['result']['text']+' '
        with self.assertRaises(ValueError):checked_moment_text(r,m)
        text='猫'*1600;r,m=records();r['response']['display_text']=m['result']['text']=text;m['result']['text_sha256']=hashlib.sha256(text.encode()).hexdigest()
        self.assertEqual(checked_moment_text(r,m),text)
    def test_malformed_roots(self):
        for bad in [None, [], '', 1]:
            r,m=records()
            with self.assertRaises(ValueError):checked_moment_text(bad,m)
            with self.assertRaises(ValueError):checked_moment_text(r,bad)

if __name__=='__main__':unittest.main()
