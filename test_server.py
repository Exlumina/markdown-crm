import json,tempfile,threading,unittest,urllib.request,urllib.error
from pathlib import Path
from server import Store,Problem,Handler,ThreadingHTTPServer,read_document,CONTENT_SECURITY_POLICY,ANNOTATION_STYLE_HASHES

def doc(value): return '# Human heading\n\nKeep this prose and [[Link]] intact.\n\n## Structured record\n```json\n'+json.dumps(value)+'\n```\n\nTrailing notes.\n'
class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name); (self.root/'records').mkdir()
        (self.root/'boards.md').write_text(doc({'schemaVersion':1,'boards':[{'id':'fundraising','name':'Startup Fundraising','stages':['New','Contacted']}]}))
        self.record={'schemaVersion':1,'id':'abc','kind':'company','name':'Example','website':'','fundingStages':['Seed'],'industries':[],'investmentRating':None,'lastCommunication':None,'notes':'','memberships':{'fundraising':{'stage':'New','order':1000}},'unknown':{'keep':True}}
        (self.root/'records/abc.md').write_text(doc(self.record)); self.store=Store(self.root)
    def tearDown(self): self.tmp.cleanup()
    def test_persistent_edit_preserves_prose_and_unknown(self):
        r=self.store.state()['records'][0]; updated=self.store.update('abc',{'expectedRevision':r['revision'],'changes':{'notes':'Met today','investmentRating':4}})
        self.assertEqual(Store(self.root).state()['records'][0]['notes'],'Met today'); self.assertEqual(updated['unknown'],{'keep':True})
        text=(self.root/'records/abc.md').read_text(); self.assertIn('Keep this prose and [[Link]] intact.',text);self.assertIn('Trailing notes.',text)
        self.assertIn('notes, investmentRating',(self.root/'Changes.md').read_text())
    def test_stale_revision(self):
        rev=self.store.state()['records'][0]['revision']; self.store.update('abc',{'expectedRevision':rev,'changes':{'name':'Changed'}})
        with self.assertRaises(Problem) as context:self.store.update('abc',{'expectedRevision':rev,'changes':{'name':'Lost'}})
        self.assertEqual(context.exception.status,409); self.assertEqual(self.store.state()['records'][0]['name'],'Changed')
    def test_invalid_stage_does_not_write(self):
        before=(self.root/'records/abc.md').read_bytes()
        with self.assertRaises(Problem): self.store.update('abc',{'expectedRevision':'','changes':{'memberships':{'fundraising':{'stage':'Missing','order':1}}}})
        self.assertEqual(before,(self.root/'records/abc.md').read_bytes())
    def test_invalid_values(self):
        for changes in ({'website':'javascript:alert(1)'},{'investmentRating':True},{'investmentRating':0},{'lastCommunication':'nope'},{'id':'replacement'}):
            with self.assertRaises(Problem): self.store.validate(changes)
    def test_corruption_does_not_write(self):
        p=self.root/'records/abc.md';p.write_text('corrupt')
        with self.assertRaises(Problem):self.store.update('abc',{'expectedRevision':'','changes':{'name':'Lost'}})
        self.assertEqual(p.read_text(),'corrupt')
    def test_create_and_reorder(self):
        r=self.store.create({'name':'New company','memberships':{'fundraising':{'stage':'New','order':2000}}}); self.assertTrue(self.store.path(r['id']).exists()); self.assertTrue(self.store.path(r['id']).name.startswith('New_company__'))
        state=self.store.state();result=self.store.reorder_boards({'expectedRevision':state['boardsRevision'],'boardId':'fundraising','stages':['Contacted','New']});self.assertEqual(result['boards'][0]['stages'],['Contacted','New'])
    def test_descriptive_filenames_and_ambiguity(self):
        original=self.root/'records/abc.md'; renamed=self.root/'records/Example__abc.md';original.rename(renamed)
        r=self.store.state()['records'][0];self.store.update('abc',{'expectedRevision':r['revision'],'changes':{'name':'Different'}})
        self.assertEqual(self.store.path('abc'),renamed.resolve());self.assertTrue(renamed.exists())
        original.write_text(doc(self.record))
        with self.assertRaises(Problem):self.store.path('abc')
    def test_symlink_escape_denied(self):
        outside=self.root/'outside.md';outside.write_text(doc(self.record));target=self.root/'records/abc.md';target.unlink();target.symlink_to(outside)
        with self.assertRaises(Problem):self.store.state()
        with self.assertRaises(Problem):self.store.path('abc')
        boards=self.root/'boards.md';boards.unlink();boards.symlink_to(outside)
        with self.assertRaises(Problem):self.store.boards()
    def test_user_edit_evidence_preserves_source(self):
        self.record['source']={'system':'Attio','capturedAt':'2025-01-01'}
        self.record['additionalFields']={'description':'Historical prose','fieldEvidence':{'name':{'sourceDate':'2025-01-01','basis':'Imported'}}}
        (self.root/'records/abc.md').write_text(doc(self.record));r=self.store.state()['records'][0]
        saved=self.store.update('abc',{'expectedRevision':r['revision'],'changes':{'name':'New name','notes':'New note'}})
        self.assertEqual(saved['source'],self.record['source']);self.assertEqual(saved['additionalFields']['description'],'Historical prose')
        self.assertEqual(saved['additionalFields']['fieldEvidence']['name']['source'],'Local CRM user edit');self.assertIn('updatedAt',saved)
    def test_notes_only_save_does_not_refresh_untouched_evidence(self):
        historical={'sourceDate':'2025-01-01','sourceRef':'Archived Attio'}
        self.record['lastCommunication']='2025-01-01T12:30:00Z'
        self.record['investmentRating']=3
        self.record['source']={'system':'Attio','capturedAt':'2025-01-01'}
        self.record['updatedAt']='2025-01-01T00:00:00Z'
        self.record['additionalFields']={'fieldEvidence':{key:dict(historical) for key in ['lastCommunication','stage','investmentRating','memberships']}}
        p=self.root/'records/abc.md';p.write_text(doc(self.record));r=self.store.state()['records'][0]
        patch={key:self.record[key] for key in ['name','kind','website','fundingStages','industries','investmentRating','lastCommunication','notes','memberships']};patch['notes']='Only notes changed'
        saved=self.store.update('abc',{'expectedRevision':r['revision'],'changes':patch})
        for key in ['lastCommunication','stage','investmentRating','memberships']:self.assertEqual(saved['additionalFields']['fieldEvidence'][key],historical)
        self.assertEqual(saved['additionalFields']['fieldEvidence']['notes']['source'],'Local CRM user edit')
        self.assertEqual(saved['source'],self.record['source'])
        self.assertTrue((self.root/'Changes.md').read_text().rstrip().endswith(' · notes'))
        before=p.read_bytes();before_mtime=p.stat().st_mtime_ns;log=(self.root/'Changes.md').read_bytes()
        unchanged=self.store.update('abc',{'expectedRevision':saved['revision'],'changes':patch})
        self.assertEqual(unchanged['revision'],saved['revision']);self.assertEqual(unchanged['updatedAt'],saved['updatedAt'])
        self.assertEqual(before,p.read_bytes());self.assertEqual(before_mtime,p.stat().st_mtime_ns);self.assertEqual(log,(self.root/'Changes.md').read_bytes())
    def test_initial_noop_creates_no_history(self):
        r=self.store.state()['records'][0];p=self.root/'records/abc.md';before=p.read_bytes()
        result=self.store.update('abc',{'expectedRevision':r['revision'],'changes':{'name':r['name']}})
        self.assertEqual(result['revision'],r['revision']);self.assertNotIn('updatedAt',result)
        self.assertEqual(before,p.read_bytes());self.assertFalse((self.root/'Changes.md').exists())
    def test_large_directory(self):
        for n in range(880):
            r=dict(self.record,id='r'+str(n),name='Company '+str(n));(self.root/'records'/f"Company_{n}__r{n}.md").write_text(doc(r))
        self.assertEqual(len(self.store.state()['records']),881)
    def test_response_action_validation_and_persistence(self):
        r=self.store.state()['records'][0]
        saved=self.store.update('abc',{'expectedRevision':r['revision'],'changes':{'communicationType':'email','replyStatus':'needs_reply','replyDueAt':'2026-10-01','nextAction':'Answer the inquiry'}})
        self.assertEqual(Store(self.root).state()['records'][0]['replyStatus'],'needs_reply')
        self.assertEqual(saved['additionalFields']['fieldEvidence']['replyStatus']['source'],'Local CRM user edit')
        self.assertEqual(saved['lastCommunication'],None)
        self.assertEqual(saved['memberships'],self.record['memberships'])
        for patch in [{'communicationType':'newsletter'},{'communicationType':[]},{'replyStatus':True},{'replyStatus':{}},{'replyDueAt':'2026-02-30'},{'replyDueAt':'2026-2-01'},{'replyDueAt':True},{'nextAction':'x'*4001}]:
            with self.assertRaises(Problem):self.store.validate(patch)
        self.store.validate({'replyStatus':None,'replyDueAt':None,'communicationType':None,'nextAction':''})
    def test_noop_response_fields_do_not_refresh_evidence(self):
        self.record['additionalFields']={'fieldEvidence':{'lastCommunication':{'sourceDate':'2025-01-01'}}}
        p=self.root/'records/abc.md';p.write_text(doc(self.record));r=self.store.state()['records'][0];before=p.read_bytes()
        saved=self.store.update('abc',{'expectedRevision':r['revision'],'changes':{'communicationType':None,'replyStatus':None,'replyDueAt':None}})
        self.assertEqual(saved['revision'],r['revision']);self.assertEqual(before,p.read_bytes())
    def test_annotation_csp_allows_only_observed_styles(self):
        self.assertNotIn('unsafe-inline',CONTENT_SECURITY_POLICY)
        self.assertNotIn('unsafe-eval',CONTENT_SECURITY_POLICY)
        self.assertIn("script-src 'self'",CONTENT_SECURITY_POLICY)
        self.assertIn("connect-src 'self'",CONTENT_SECURITY_POLICY)
        self.assertIn("frame-ancestors 'none'",CONTENT_SECURITY_POLICY)
        self.assertEqual(len(ANNOTATION_STYLE_HASHES),2)
        for value in ANNOTATION_STYLE_HASHES:self.assertIn("'"+value+"'",CONTENT_SECURITY_POLICY)
    def test_foreign_origin_and_host(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.store=self.store
        t=threading.Thread(target=server.serve_forever,daemon=True);t.start();url=f'http://127.0.0.1:{server.server_port}'
        try:
            request=urllib.request.Request(url+'/api/records',data=b'{"name":"Injected"}',headers={'Content-Type':'application/json','Origin':'https://foreign.example'},method='POST')
            with self.assertRaises(urllib.error.HTTPError) as c:urllib.request.urlopen(request)
            self.assertEqual(c.exception.code,403)
            request=urllib.request.Request(url+'/api/state',headers={'Host':'foreign.example'})
            with self.assertRaises(urllib.error.HTTPError) as c:urllib.request.urlopen(request)
            self.assertEqual(c.exception.code,403)
            request=urllib.request.Request(url+'/api/records',data=b'{"name":"Allowed"}',headers={'Content-Type':'application/json','Origin':url},method='POST')
            with urllib.request.urlopen(request) as response:self.assertEqual(response.status,201)
        finally:server.shutdown();server.server_close();t.join()
if __name__=='__main__':unittest.main()
