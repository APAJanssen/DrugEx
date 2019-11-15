#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""This file is used for dataset construction.

It contains two dataset as follows:

1. ZINC set: it is used for pre-training model
2. A2AR set: it is used for fine-tuning model and training predictor
"""

import os
import re

import click
import numpy as np
import pandas as pd
from rdkit import Chem
from tqdm import tqdm

from drugex import environ
from drugex.util import Voc


SUB_RE = re.compile(r'\[\d+')


def corpus(input: str, out: str, *, vocab_path: str):
    """Constructing the molecular corpus by splitting each SMILES into
    a range of tokens contained in vocabulary.

    Arguments:
        input : the path of tab-delimited data file that contains CANONICAL_SMILES.
        out : the path for vocabulary (containing all of tokens for SMILES construction)
            and output table (including CANONICAL_SMILES and whitespace delimited token sentence)
    """
    df = pd.read_table(input).CANONICAL_SMILES.dropna()
    voc = Voc(vocab_path)
    canons = []
    tokens = []
    smiles = set()
    it = tqdm(df, desc='Reading SMILES')
    for smile in it:
        # replacing the radioactive atom into nonradioactive atom
        smile = SUB_RE.sub('[', smile)
        # reserving the largest one if the molecule contains more than one fragments,
        # which are separated by '.'.
        if '.' in smile:
            frags = smile.split('.')
            ix = np.argmax([len(frag) for frag in frags])
            smile = frags[ix]
            # TODO replace with: smile = max(frags, key=len)
        # if it doesn't contain carbon atom, it cannot be drug-like molecule, just remove
        if smile.count('C') + smile.count('c') < 2:
            continue
        if smile in smiles:
            it.write('duplicate: {}'.format(smile))
        smiles.add(smile)
    # collecting all of the tokens in the sentences for vocabulary construction.
    words = set()
    it = tqdm(smiles, desc='Collecting tokens')
    for smile in it:
        try:
            token = voc.tokenize(smile)
            if len(token) <= 100:
                words.update(token)
                canons.append(Chem.CanonSmiles(smile, 0))
                tokens.append(' '.join(token))
        except Exception as e:
            it.write('{} {}'.format(e, smile))
    # persisting the vocabulary on the hard drive.
    with open(out + '_voc.txt', 'w') as file:
        file.write('\n'.join(sorted(words)))

    # saving the canonical smiles and token sentences as a table into hard drive.
    log = pd.DataFrame()
    log['CANONICAL_SMILES'] = canons
    log['SENT'] = tokens
    log.drop_duplicates(subset='CANONICAL_SMILES')
    log.to_csv(out + '_corpus.txt', sep='\t', index=None)


def ZINC(folder: str, out: str):
    """Uniformly random selecting molecule from ZINC database for Construction of ZINC set,
    which is used for pre-trained model training.

    Arguments:
        folder : the directory of the ZINC database, it contains all of the molecules
            that are separated into different files based on the logP and molecular weight.
        out : the file path of output dataframe, it contains all of randomly selected molecules,
            also including its SMILES string, logP and molecular weight
    """
    files = os.listdir(folder)
    points = [(i, j) for i in range(200, 600, 25) for j in np.arange(-2, 6, 0.5)]
    select = pd.DataFrame()
    for symbol in tqdm([i+j for i in 'ABCDEFGHIJK' for j in 'ABCDEFGHIJK']):
        zinc = pd.DataFrame()
        for fname in files:
            if not fname.endswith('.txt'): continue
            if not fname.startswith(symbol): continue
            df = pd.read_table(folder+fname)[['mwt', 'logp', 'smiles']]
            df.columns = ['MWT', 'LOGP', 'CANONICAL_SMILES']
            zinc = zinc.append(df)
        for mwt, logp in points:
            df = zinc[(zinc.MWT > mwt) & (zinc.MWT <= (mwt + 25))]
            df = df[(df.LOGP > logp) & (df.LOGP <= (logp+0.5))]
            if len(df) > 2500:
                df = df.sample(2500)
            select = select.append(df)
    select.to_csv(out, sep='\t', index=None)


def A2AR(input_path: str, output_path: str):
    """Construction of A2AR set, which is used for fine-tuned model and predictor training.
    Arguments:
        input_path : the path of tab-delimited data file that contains CANONICAL_SMILES.
        output_path : the path saving the refined data after filtering the invalid data,
            including removing molecule contained metal atom, reserving the largest fragments,
            and replacing the nitrogen electrical group to nitrogen atom "N".
    """
    df = pd.read_table(input_path)
    df = df[environ.PAIR]
    df = df.dropna(subset=environ.PAIR[:-1]) #FIXME: this should use the name of the column rather than an index
    for i, row in df.iterrows():
        # replacing the nitrogen electrical group to nitrogen atom "N"
        smile = row['CANONICAL_SMILES'].replace('[NH+]', 'N').replace('[NH2+]', 'N').replace('[NH3+]', 'N')
        # removing the radioactivity of each atom
        smile = re.sub('\[\d+', '[', smile)
        # reserving the largest fragments
        if '.' in smile:
            frags = smile.split('.')
            ix = np.argmax([len(frag) for frag in frags])
            smile = frags[ix]
        # Transforming into canonical SMILES based on the Rdkit built-in algorithm.
        df.loc[i, 'CANONICAL_SMILES'] = Chem.CanonSmiles(smile, 0)
        # removing molecule contained metal atom
        if '[Au]' in smile or '[As]' in smile or '[Hg]' in smile or '[Se]' in smile or smile.count('C') + smile.count('c') < 2:
            df = df.drop(i)
    # df = df.drop_duplicates(subset='CANONICAL_SMILES')
    df.to_csv(output_path, index=False, sep='\t')


@click.command()
@click.option('-d', '--data-directory', type=click.Path(dir_okay=True, file_okay=False), required=True)
@click.option('-e', '--environment-data-file', type=click.Path(dir_okay=False, file_okay=True), required=True)
@click.option('-v', '--vocabulary-file', default='voc.txt', type=click.Path(dir_okay=False, file_okay=True))
def main(data_directory, environment_data_file, vocabulary_file):
    zinc_output_path = os.path.join(data_directory, 'ZINC.txt')
    zinc_directory = os.path.join(data_directory, 'zinc')
    if os.path.exists(zinc_directory):
        click.echo("Compiling {0}".format(zinc_output_path))
        ZINC(folder=zinc_directory, out=zinc_output_path)
        click.echo("Done.")
    else:
        click.echo('Missing ZINC folder: {0} \nThe default ZINC output file will be used: {1}'.format(zinc_directory, zinc_output_path), err=True)

    click.echo("Generating ZINC corpus from: {0}".format(zinc_output_path))
    zinc_processed = os.path.exists(os.path.join(data_directory, 'zinc_corpus.txt')) \
                 and os.path.exists(os.path.join(data_directory, 'zinc_voc.txt'))
    if os.path.exists(zinc_output_path) and not zinc_processed:
        corpus(zinc_output_path, os.path.join(data_directory, 'zinc'), vocab_path=os.path.join(data_directory, vocabulary_file))
        click.echo("Done.")
    elif zinc_processed:
        click.echo('ZINC data was already processed (corpus and vocabulary files generated). Skipping...', err=True)
        pass
    else:
        raise FileNotFoundError('Missing ZINC output file: {}'.format(zinc_output_path))

    click.echo("Parsing raw CHEMBL data at {0}".format(environment_data_file))
    environment_data_file = os.path.join(data_directory, environment_data_file)
    output_path = os.path.join(data_directory, 'FT_ENV_data.txt')
    if os.path.exists(environment_data_file):
        A2AR(environment_data_file, output_path)
        click.echo("Done.")
    else:
        raise FileNotFoundError('Missing environment data path: {0}'.format(environment_data_file))

if __name__ == '__main__':
    main()
