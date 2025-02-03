import re
import string


def normalize_str(string_in):
    # remove punctuation
    punctuation = re.compile(f"[{string.punctuation}]")
    string_in = re.sub(punctuation, " ", string_in)

    # normalize whitespace
    whitespace = re.compile(f"[{string.whitespace}]+")
    string_in = re.sub(whitespace, " ", string_in)

    # remove leading and trailing whitespace
    string_in = string_in.strip(string.whitespace)

    return string_in


def str_compare(s1, s2, ignore_case=True):
    s1 = normalize_str(s1)
    s2 = normalize_str(s2)
    if ignore_case:
        s1 = s1.lower()
        s2 = s2.lower()
    return s1 == s2
