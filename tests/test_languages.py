"""Integration: each language runs end-to-end (real gVisor sandboxes)."""
import pytest

# reruns absorb the documented host-load transient (a real failure fails all reruns)
pytestmark = [pytest.mark.integration, pytest.mark.flaky(reruns=2, reruns_delay=1)]

PROGRAMS = {
    "python": "import sys; print('OUT', sys.stdin.read().strip().upper())",
    "javascript": ("const d=require('fs').readFileSync(0,'utf8').trim();"
                   "console.log('OUT', d.toUpperCase())"),
    "ruby": "puts \"OUT #{STDIN.read.strip.upcase}\"",
    "cpp": ('#include <iostream>\n#include <string>\n#include <algorithm>\n'
            'int main(){std::string s;std::getline(std::cin,s);'
            'std::transform(s.begin(),s.end(),s.begin(),::toupper);'
            'std::cout<<"OUT "<<s<<"\\n";}'),
    "rust": ('use std::io::Read;fn main(){let mut s=String::new();'
             'std::io::stdin().read_to_string(&mut s).ok();'
             'println!("OUT {}", s.trim().to_uppercase());}'),
    "go": ('package main\nimport("bufio";"fmt";"os";"strings")\n'
           'func main(){r:=bufio.NewReader(os.Stdin);l,_:=r.ReadString(\'\\n\');'
           'fmt.Println("OUT", strings.ToUpper(strings.TrimSpace(l)))}'),
}


@pytest.mark.parametrize("lang", sorted(PROGRAMS))
async def test_language_hello(run_code, lang):
    r = await run_code(lang, PROGRAMS[lang], stdin="abc", timeout=None)
    assert r.exit_code == 0, f"{lang} stderr: {r.stderr[:200]}"
    assert "OUT ABC" in r.stdout


async def test_compile_error_reports_compile_stage(run_code):
    # not valid C++ -> compile step fails, no run
    r = await run_code("cpp", "this is not c++")
    assert r.stage == "compile"
    assert r.exit_code != 0


async def test_compiled_hostile_contained_at_runtime(run_code):
    # C++ that tries to open a socket at runtime -> network unreachable
    code = ('#include <sys/socket.h>\n#include <netinet/in.h>\n#include <cstdio>\n'
            'int main(){int s=socket(AF_INET,SOCK_STREAM,0);'
            'sockaddr_in a{};a.sin_family=AF_INET;a.sin_port=htons(80);'
            'int rc=connect(s,(sockaddr*)&a,sizeof a);'
            'printf(rc==0?"OPEN\\n":"blocked\\n");}')
    r = await run_code("cpp", code)
    assert "OPEN" not in r.stdout and "blocked" in r.stdout
