import zipfile

from brunder import bweb

HEADER = '"SG_UF";"CD_MUNICIPIO";"NR_ZONA";"NR_SECAO";"DS_CARGO_PERGUNTA";"DT_BU_RECEBIDO";"NR_VOTAVEL";"QT_VOTOS"'


def test_receipt_times_are_read_day_first(tmp_path):
    # 2 Oct 2022 is ambiguous (02/10); a format guess turned it into 10 Feb and scrambled the count order
    rows = ['"AC";"01392";"1";"3";"Presidente";"02/10/2022 17:34:43";"13";"48"',
            '"AC";"01392";"1";"4";"Presidente";"03/10/2022 00:20:45";"22";"30"']
    z = tmp_path / "bweb_1t_AC.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("bweb_1t_AC.csv", "\n".join([HEADER, *rows]).encode("latin-1"))
    d = bweb._parse(z)
    assert [str(t) for t in d["received"]] == ["2022-10-02 17:34:43", "2022-10-03 00:20:45"]
