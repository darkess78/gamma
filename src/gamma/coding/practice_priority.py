def _count(v):
    return v if type(v) is int and v >= 0 else 0

def prioritize(tasks, skills):
    if not isinstance(skills, dict):
        skills = {}
    def key(t):
        c = skills.get(getattr(t, 'skill', None))
        if not isinstance(c, dict):
            c = {}
        failed = _count(c.get('failed'))
        repairs = _count(c.get('repairs'))
        indep = _count(c.get('independently_evaluated'))
        return (0 if failed > 0 else 1, 0 if repairs > 0 else 1, 0 if indep == 0 else 1, -repairs)
    return sorted(tasks, key=key)
