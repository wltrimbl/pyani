#!/usr/bin/env python3
#
# pyani.py
#
# This script uses the pyani module to produce ANI analyses and classifications
# of prokaryotic genome sequences (draft or complete).
#
# (c) The James Hutton Institute 2016-2017
# Author: Leighton Pritchard
#
# Contact:
# leighton.pritchard@hutton.ac.uk
#
# Leighton Pritchard,
# Information and Computing Sciences,
# James Hutton Institute,
# Errol Road,
# Invergowrie,
# Dundee,
# DD6 9LH,
# Scotland,
# UK
#
# The MIT License
#
# Copyright (c) 2016-2017 The James Hutton Institute
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import logging
import os
import re
import shutil
import sys
import time
import traceback

from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter
from collections import defaultdict, namedtuple

from pyani import __version__, download

class PyaniDownloadException(Exception):
    """General exception for downloading"""
    def __init__(self, msg="Error in download subcommand"):
        Exception.__init__(self, msg)


# Report last exception as string
def last_exception():
    """Returns last exception as a string, or use in logging."""
    exc_type, exc_value, exc_traceback = sys.exc_info()
    return ''.join(traceback.format_exception(exc_type, exc_value,
                                              exc_traceback))


# Process command-line
def parse_cmdline(args):
    """Parse command-line arguments for script.
    The script offers a single main parser, with subcommands for the actions:
    
    classify - produce a graph-based classification of each input genome
    """
    # Main parent parser
    parser_main = ArgumentParser(prog="pyani.py",
                                 formatter_class=ArgumentDefaultsHelpFormatter)
    subparsers = parser_main.add_subparsers(title="subcommands",
                                            description="valid subcommands",
                                            help="additional help")

    # A 'common' parser, with shared options for all subcommands
    # Not all commands require input or output directories, but all commands
    # support verbose output, and logging.
    parser_common = ArgumentParser(add_help=False)
    parser_common.add_argument('-l', '--logfile', dest='logfile',
                               action='store', default=None,
                               help='logfile location')
    parser_common.add_argument('-v', '--verbose', action='store_true',
                               dest='verbose', default=False,
                               help='report verbose progress to log')

    # Subcommand parsers
    # Download genomes from NCBI
    parser_download = subparsers.add_parser('download',
                                            parents=[parser_common],
                                            formatter_class=\
                                            ArgumentDefaultsHelpFormatter)
    # Classify pyani output into genomotypes
    parser_classify = subparsers.add_parser('classify',
                                            parents=[parser_common],
                                            formatter_class=\
                                            ArgumentDefaultsHelpFormatter)

    # DOWNLOAD: Genome download options
    # Output directory, positional and required
    parser_download.add_argument(action='store',
                                 dest='outdir', default=None,
                                 help='output directory')
    # Required arguments for NCBI query
    parser_download.add_argument("-t", "--taxon", dest="taxon",
                                 action="store", default=None,
                                 help="NCBI taxonomy IDsr (required)",
                                 required=True)
    parser_download.add_argument("--email", dest="email", required=True,
                                 action="store", default=None,
                                 help="Email associated with NCBI queries " +\
                                 "(required)")
    # Arguments controlling connection to NCBI for download
    parser_download.add_argument("--retries", dest="retries",
                                 action="store", default=20,
                                 help="Number of Entrez retry attempts per " +\
                                 "request")
    parser_download.add_argument("--batchsize", dest="batchsize",
                                 action="store", default=10000,
                                 help="Entrez record return batch size")
    parser_download.add_argument("--timeout", dest="timeout",
                                 action="store", default=10,
                                 help="Timeout for URL connection (s)")
    # Arguments controlling local filehandling
    parser_download.add_argument("-f", "--force", dest="force",
                                 action="store_true", default=False,
                                 help="Allow download to existing directory")
    parser_download.add_argument("--noclobber", dest="noclobber",
                                 action="store_true", default=False,
                                 help="Don't replace existing files")


    # CLASSIFY: Genome classification options
    # Input directory, positional and required
    parser_classify.add_argument(action='store',
                                 dest='indir', default=None,
                                 help='input directory')
    # Output directory, defaults to input directory indir
    parser_classify.add_argument('-o', '--outdir', action='store',
                                 dest='outdir', default=None,
                                 help='output directory')
    # Label file, defaults to indir/labels.txt
    parser_classify.add_argument('--labels', dest='labelfile',
                                 action='store', default=None,
                                 help='file with labels for input genomes')
    # Parameters for classification: minimum %coverage, %identity,
    # and the resolution of thresholds to test
    parser_classify.add_argument('--cov_min', dest='cov_min',
                                 action='store', type=float, default=0.5,
                                 help='minimum %%coverage for an edge')
    parser_classify.add_argument('--id_min', dest='id_min',
                                 action='store', type=float, default=0.8,
                                 help='minimum %%identity for an edge')
    parser_classify.add_argument('--resolution', dest='resolution',
                                 action='store', type=int, default=1500,
                                 help='number of identity thresholds to test')
    
    # Parse arguments
    return parser_main


# DOWNLOAD
# Download sequence/class/label data from NCBI
def subcmd_download(args, logger):
    """Download all assembled genomes beneath a passed taxon ID from NCBI."""
    # Create output directory
    if os.path.isdir(args.outdir):
        logger.warning("Output directory %s exists", args.outdir)
        if not args.force:
            raise PyaniDownloadException("Will not overwrite existing " +
                                         "directory {0}".format(args.outdir))
        elif args.force and not args.noclobber:
            # Delete old directory and start again
            logger.warning("Overwrite forced. " +
                           "Removing {0} ".format(args.outdir) + 
                           "and everything below it")
            shutil.rmtree(args.outdir)
        else:
            logger.warning("Keeping existing directory, skipping existing " +
                           "files.")
    os.makedirs(args.outdir, exist_ok=True)
    
    # Set Entrez email
    download.set_ncbi_email(args.email)
    logger.info("Set Entrez email address: {0}".format(args.email))
    
    # Get list of taxon IDs to download
    taxon_ids = download.split_taxa(args.taxon)
    logger.info("Taxa received: {0}".format(taxon_ids))

    # Get assembly UIDs for each taxon
    asm_dict = dict()
    for tid in taxon_ids:
        asm_uids = download.get_asm_uids(tid, args.retries)
        logger.info("Query: " +\
                    "{0}\n\t\tasm count: {1}\n\t\tUIDs: {2}".format(*asm_uids))
        asm_dict[tid] = asm_uids.asm_ids
    print(asm_dict)

    # Download contigs and hashes for each assembly UID
    for tid, uids in asm_dict.items():
        logger.info("\nDownloading contigs for Taxon ID %s", tid)
        for uid in uids:
            # Obtain eSummary            
            logger.info("Get eSummary information for UID %s", uid)
            esummary, filestem = download.get_ncbi_esummary(uid, args.retries)
            logger.info("\tTaxid: %s", esummary['SpeciesTaxid'])
            logger.info("\tAccession: %s", esummary['AssemblyAccession'])
            logger.info("\tName: %s", esummary['AssemblyName'])

            # Parse classification
            uid_class = download.get_ncbi_classification(esummary)
            logger.info("\tOrganism: %s", uid_class.organism)
            logger.info("\tGenus: %s", uid_class.genus)
            logger.info("\tSpecies: %s", uid_class.species)
            logger.info("\tStrain: %s", uid_class.strain)

            # Make label/class text
            labeltxt, classtxt = download.create_labels(uid_class, filestem)
            logger.info("\tLabel: %s", labeltxt)
            logger.info("\tClass: %s", classtxt)
    
            # Obtain URLs
            ftpstem="ftp://ftp.ncbi.nlm.nih.gov/genomes/all"
            suffix="genomic.fna.gz"
            logger.info("Retrieving URLs for %s", filestem)
            dlstatus = download.retrieve_genome_and_hash(filestem,
                                                         suffix,
                                                         ftpstem,
                                                         args.outdir,
                                                         args.timeout)
            if not dlstatus.refseq:
                logger.warning("Downloaded GenBank alternative assembly")
            logger.info("Used URL: %s", dlstatus.url)
            if dlstatus.skipped:
                logger.warning("File %s exists, did not download",
                               dlstatus.outfname)
            else:
                logger.info("Wrote assembly to: %s", dlstatus.outfname)
                logger.info("Wrote MD5 hashes to: %s", dlstatus.outfhash)

            # Check hash for the download
            hashstatus = download.check_hash(dlstatus.outfname,
                                             dlstatus.outfhash)
            logger.info("Local MD5 hash: %s", hashstatus.localhash)
            logger.info("NCBI MD5 hash: %s", hashstatus.filehash)
            if hashstatus.passed:
                logger.info("MD5 hash check passed")
            else:
                logger.warning("MD5 hash check failed.")
                


# CLASSIFY
# Classify input genomes on basis of ANI coverage and identity output
def subcmd_classify(args, logger):
    """Take pyani output, and generate a series of classifications of the
    input data.
    """
    raise NotImplementedError


    
###
# Run as script
if __name__ == '__main__':

    # Parse command-line
    parser = parse_cmdline(sys.argv)
    args = parser.parse_args()

    # If no arguments provided, show usage and drop out
    if len(sys.argv) == 1:
        print("pyani version: {0}".format(__version__))
        parser.print_help()
        sys.exit(1)

    # Set up logging
    logger = logging.getLogger('pyani.py: %s' % time.asctime())
    t0 = time.time()
    logger.setLevel(logging.DEBUG)
    err_handler = logging.StreamHandler(sys.stderr)
    err_formatter = logging.Formatter('%(levelname)s: %(message)s')
    err_handler.setFormatter(err_formatter)

    # Was a logfile specified? If so, use it
    if args.logfile is not None:
        try:
            logstream = open(args.logfile, 'w')
            err_handler_file = logging.StreamHandler(logstream)
            err_handler_file.setFormatter(err_formatter)
            err_handler_file.setLevel(logging.INFO)
            logger.addHandler(err_handler_file)
        except:
            logger.error('Could not open %s for logging' %
                         args.logfile)
            logger.error(last_exception())
            sys.exit(1)

    # Do we need verbosity?
    if args.verbose:
        err_handler.setLevel(logging.INFO)
    else:
        err_handler.setLevel(logging.WARNING)
    logger.addHandler(err_handler)

    # Report arguments, if verbose
    logger.info('Processed arguments: %s' % args)
    logger.info('command-line: %s' % ' '.join(sys.argv))

    # Define subcommand main functions, and distribute on basis of subcommand
    # NOTE: Halting raised during running subcommands are caught and logged
    #       here - they do not generally need to be caught before this point.
    subcmd = sys.argv[1]
    subcmds = {'classify': subcmd_classify,
               'download': subcmd_download}
    try:
        subcmds[subcmd](args, logger)
    except KeyError:
        logger.error("Subcommand {0} not recognised (exiting)".format(subcmd))
        sys.exit(1)
    except NotImplementedError:
        logger.error("Subcommand {0} not yet ".format(subcmd) +\
                     "implemented (exiting)")
        sys.exit(1)
    except:
        logger.error("Could not execute subcommand {0}".format(subcmd))
        logger.error(last_exception())
        sys.exit(1)



    # Exit cleanly (POSIX)
    sys.exit(0)
